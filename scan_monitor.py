"""Local, single-scan progress log and bounded crash recovery."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
from snapshot_io import replace_snapshot

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
BASE='http://127.0.0.1:8765'

def now():return datetime.now(timezone.utc).isoformat()

def recoverable(job):
    return job.get('status') in ('running','queued') and not job.get('pause_requested') and not job.get('cancel_requested') and bool(job.get('checkpoint'))

def snapshot(job):
    index=job.get('checkpoint',{}).get('hospital_index',0)
    hospitals=job.get('hospitals',[])
    if job.get('status')=='completed':index=len(hospitals)
    return {'scan_id':job['id'],'checked_at':now(),'monitor_pid':os.getpid(),'status':job['status'],
        'centers_total':len(hospitals),'centers_processed':job.get('completed_centers',min(index,len(hospitals))),
        'active_centers':job.get('active_centers',[]),
        'current_center':hospitals[index]['name'] if index<len(hospitals) else '',
        'current_url':job.get('current_url',''),'pages':len(job.get('pages',[])),
        'price_findings':len(job.get('results',[])),
        'contacts':len(job.get('contacts',[])),'active_seconds':round(job.get('active_seconds',0)),
        'page_statuses':dict(Counter(p.get('status','unknown') for p in job.get('pages',[]))),
        'enrichment_status':job.get('enrichment_status','pending'),'error':job.get('error','')}

def write_json(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    replace_snapshot(temporary,path)

def connection_refused():
    with socket.socket() as sock:
        sock.settimeout(3)
        return sock.connect_ex(('127.0.0.1',8765)) in (10061,111)

def start_server():
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    with (DATA/'monitor-server-out.log').open('ab') as out,(DATA/'monitor-server-err.log').open('ab') as err:
        return subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port','8765'],cwd=ROOT,stdout=out,stderr=err,creationflags=flags)

def resume_after_crash(client,scan_id):
    token=client.get('/api/session').json()['token']
    response=client.post('/api/scans/'+scan_id+'/resume',headers={'x-app-token':token})
    response.raise_for_status()

def monitor(scan_id,interval=30):
    source=DATA/(scan_id+'.json')
    output=DATA/'monitoring'/scan_id;output.mkdir(parents=True,exist_ok=True)
    lock=output/'monitor.lock'
    handle=lock.open('a+b')
    if lock.stat().st_size==0:handle.write(b'0');handle.flush()
    if os.name=='nt':
        import msvcrt
        handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    def event(kind,**details):
        with (output/'events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'at':now(),'kind':kind,**details},ensure_ascii=False)+'\n')
    attempts=0;was_active=False;previous=None;seen_pages=0;errors=set();stalled_reported=False
    previous_report=output/'completion.txt'
    if previous_report.exists():previous_report.replace(output/('previous-completion-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')+'.txt'))
    event('monitor_started',pid=os.getpid())
    try:
        with httpx.Client(base_url=BASE,timeout=15,trust_env=False) as client:
            while True:
                try:
                    job=json.loads(source.read_text(encoding='utf-8'))
                    state=snapshot(job);state['recovery_attempts']=attempts
                    write_json(output/'status.json',state)
                    for page in job.get('pages',[]):
                        if page.get('status') not in ('error','blocked','partial','unsupported','discovery warning'):continue
                        key=(page.get('hospital'),page.get('url'),page.get('status'),page.get('detail'))
                        if key in errors:continue
                        errors.add(key);event('source_issue',hospital=page.get('hospital'),url=page.get('url'),status=page.get('status'),detail=page.get('detail','')[:1500])
                    seen_pages=len(job.get('pages',[]))
                    marker=(state['status'],state['centers_processed'],state['price_findings'],tuple(w['index'] for w in state['active_centers']))
                    if marker!=previous:
                        event('progress',**state);previous=marker
                    age=time.time()-source.stat().st_mtime
                    if recoverable(job) and age>900 and not stalled_reported:
                        event('attention_needed',reason='No saved checkpoint for 15 minutes; inspect before interrupting a potentially large document.');stalled_reported=True
                    if age<120:stalled_reported=False
                    terminal=job['status'] in ('failed','cancelled') or (job['status']=='completed' and job.get('enrichment_status') in ('ready','failed'))
                    if terminal:
                        state['monitor_finished_at']=now();write_json(output/'status.json',state)
                        report=[f"Scan {scan_id}",f"Status: {job['status']}",f"Centers processed: {state['centers_processed']} / {state['centers_total']}",f"Pages: {state['pages']}",f"Raw price findings: {state['price_findings']}",f"Contact findings: {state['contacts']}",f"Review preparation: {state['enrichment_status']}",f"Recovery attempts: {attempts}","No findings were approved or applied by this monitor.","Source failures and recovery actions are recorded in events.jsonl."]
                        (output/'completion.txt').write_text('\n'.join(report)+'\n',encoding='utf-8');event('monitor_finished',**state);return
                    if attempts<3 and recoverable(job) and connection_refused():
                        attempts+=1;was_active=True;process=start_server();event('server_restarted',pid=process.pid,attempt=attempts)
                        time.sleep(8)
                        restored=json.loads(source.read_text(encoding='utf-8'))
                        if restored['status']=='paused' and not restored.get('pause_requested') and not restored.get('cancel_requested'):
                            resume_after_crash(client,scan_id);event('checkpoint_resumed')
                    elif was_active and job['status']=='paused' and not job.get('pause_requested') and not job.get('cancel_requested') and attempts<3:
                        attempts+=1;resume_after_crash(client,scan_id);event('checkpoint_resumed',attempt=attempts)
                    was_active=recoverable(job)
                except Exception as exc:
                    event('monitor_error',error=type(exc).__name__,detail=str(exc)[:1500])
                time.sleep(interval)
    finally:handle.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('scan_id');args=parser.parse_args()
    if len(args.scan_id)!=32 or any(c not in '0123456789abcdef' for c in args.scan_id):parser.error('Invalid scan ID')
    monitor(args.scan_id)
