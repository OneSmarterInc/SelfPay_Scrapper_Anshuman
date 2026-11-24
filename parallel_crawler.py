"""Four-center coordinator with independently resumable crawl frontiers."""
import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone
import time
from urllib.parse import urlsplit

COLLECTIONS=('pages','results','contacts','entities')

def host(hospital):
    return (urlsplit(hospital['url']).hostname or '').lower().removeprefix('www.')

def child_job(job,index):
    hospital=job['hospitals'][index]
    return {'id':str(job.get('id','scan'))+':'+str(index),'hospitals':[hospital],
        'baseline_records':[row for row in job.get('baseline_records',[]) if row['id']==hospital.get('record_id')],
        'max_pages':job.get('max_pages'),'max_depth':job.get('max_depth'),
        'pages':[],'results':[],'contacts':[],'entities':[],'warnings':[],
        'status':'queued','pause_requested':False,'cancel_requested':False,'current_url':''}

def migrate(job):
    old=job.get('checkpoint') or {}
    if old.get('version')==2:return
    index=old.get('hospital_index',0)
    count=len(job['hospitals'])
    state={'version':2,'hospital_index':index,'completed_indices':list(range(index)),
        'pending_indices':list(range(index,count)),'workers':{}}
    if index<count and old.get('queue') is not None:
        child=child_job(job,index);hospital=job['hospitals'][index]
        checkpoint=copy.deepcopy(old);checkpoint['hospital_index']=0
        checkpoint['lengths']={key:0 for key in (*COLLECTIONS,'warnings')}
        for key in ('pages','results','contacts'):
            for offset,item in enumerate(job.get(key,[])):
                belongs=(item.get('provider_index')==index if 'provider_index' in item else item.get('hospital')==hospital['name']) if key=='pages' else (item.get('record_id') or '')==(hospital.get('record_id') or '') and item.get('hospital')==hospital['name']
                if not belongs:continue
                item['scan_provider_index']=index
                row=copy.deepcopy(item)
                if key=='pages':row['provider_index']=0
                child[key].append(row)
                if offset<old.get('lengths',{}).get(key,0):checkpoint['lengths'][key]+=1
        child['checkpoint']=checkpoint
        state['workers'][str(index)]=child
        state['pending_indices'].remove(index)
    job['checkpoint']=state
    job['concurrency']=4
    job['completed_centers']=len(state['completed_indices'])

def review_key(collection,row,index=None):
    identity={'collection':collection,'owner':row.get('scan_provider_index') if index is None else index}
    fields=('id',) if collection=='results' else ('record_id','field','value','source_url','entity') if collection=='contacts' else ('name','address','url')
    identity.update({key:row.get(key) for key in fields})
    return hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def remember_review(job,collection,row):
    if job.get('checkpoint',{}).get('version')!=2:return
    job.setdefault('review_overrides',{})[review_key(collection,row)]={key:row[key] for key in ('review','reviewed_at') if key in row}

def merge(job,index,child):
    for key in COLLECTIONS:
        for previous in job.get(key,[]):
            if key!='pages' and previous.get('reviewed_at'):remember_review(job,key,previous)
        kept=[row for row in job.get(key,[]) if row.get('scan_provider_index')!=index]
        for original in child.get(key,[]):
            if key!='pages':original.update(job.get('review_overrides',{}).get(review_key(key,original,index),{}))
            row=copy.deepcopy(original);row['scan_provider_index']=index
            if key=='pages':row['provider_index']=index
            kept.append(row)
        job[key]=kept
    for warning in child.get('warnings',[]):
        if warning not in job['warnings']:job['warnings'].append(warning)
    job['hospitals'][index]=child['hospitals'][0]

def progress(job):
    state=job['checkpoint'];done=set(state['completed_indices'])
    index=0
    while index in done:index+=1
    state['hospital_index']=index
    job['completed_centers']=len(done)
    job['active_centers']=[{'index':int(key),'name':child['hospitals'][0]['name'],
        'record_id':child['hospitals'][0].get('record_id',''),'status':child['status'],
        'current_url':child.get('current_url',''),'pages':len(child.get('pages',[])),
        'prices':len(child.get('results',[]))} for key,child in sorted(state['workers'].items(),key=lambda pair:int(pair[0]))]
    job['current_url']=next((row['current_url'] for row in job['active_centers'] if row['current_url']),'')

async def run(job,persist,serial):
    migrate(job)
    job.setdefault('warnings',[])
    for key in COLLECTIONS:job.setdefault(key,[])
    job.setdefault('started_at',datetime.now(timezone.utc).isoformat())
    job.pop('finished_at',None);job.pop('error',None)
    job['status']='running';job['extraction_version']=7
    state=job['checkpoint'];tasks={}
    started=time.monotonic();elapsed=float(job.get('active_seconds',0))
    limits={'browser':asyncio.Semaphore(2),'pdf':asyncio.Semaphore(1)}
    def save():
        progress(job)
        job['active_seconds']=elapsed+max(0,time.monotonic()-started)
        job['timer_updated_at']=datetime.now(timezone.utc).isoformat()
        persist(job)
    def child_save(index,child):
        child['pause_requested']=bool(job.get('pause_requested'))
        child['cancel_requested']=bool(job.get('cancel_requested'))
        merge(job,index,child);save()
        child['pause_requested']=bool(job.get('pause_requested'))
        child['cancel_requested']=bool(job.get('cancel_requested'))
    def launch(index):
        child=state['workers'].setdefault(str(index),child_job(job,index))
        child.pop('error',None)
        child.update(status='queued',pause_requested=False,cancel_requested=False,_worker_stop_requested=False)
        task=asyncio.create_task(serial(child,lambda current:child_save(index,current),limits))
        tasks[task]=index
    save()
    try:
        while True:
            stopping=job.get('pause_requested') or job.get('cancel_requested')
            for child in state['workers'].values():
                child['pause_requested']=bool(job.get('pause_requested'))
                child['cancel_requested']=bool(job.get('cancel_requested'))
            if not stopping:
                busy={host(job['hospitals'][index]) for index in tasks.values()}
                waiting=[int(key) for key in state['workers'] if int(key) not in tasks.values()]+state['pending_indices'][:]
                for index in waiting:
                    if len(tasks)>=4:break
                    website=host(job['hospitals'][index])
                    if website in busy:continue
                    if index in state['pending_indices']:state['pending_indices'].remove(index)
                    launch(index);busy.add(website)
            if not tasks:break
            finished,_=await asyncio.wait(tasks,timeout=0.2,return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                index=tasks.pop(task);task.result()
                child=state['workers'][str(index)]
                if child['status']=='completed':
                    state['completed_indices'].append(index)
                    del state['workers'][str(index)]
                elif child['status']=='failed':
                    raise RuntimeError(child.get('error','Center worker failed'))
                elif child['status']=='paused' and not stopping:
                    job['pause_requested']=True
                save()
        job['status']='cancelled' if job.get('cancel_requested') else 'paused' if job.get('pause_requested') else 'completed'
        if job['status']=='completed':
            job['entities']=list({(row.get('name'),row.get('address')):row for row in job['entities']}.values())
    except asyncio.CancelledError:
        job['status']='paused'
        raise
    except Exception as exc:
        job.update(status='failed',error=str(exc))
    finally:
        for child in state['workers'].values():child['_worker_stop_requested']=True
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        job['finished_at']=datetime.now(timezone.utc).isoformat()
        save()
