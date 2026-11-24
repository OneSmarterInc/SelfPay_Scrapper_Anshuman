from scan_monitor import recoverable,snapshot

def test_monitor_honors_user_pause_cancel_and_terminal_states():
    base={'status':'running','checkpoint':{'hospital_index':2}}
    assert recoverable(base)
    for change in [{'pause_requested':True},{'cancel_requested':True},{'status':'paused'},{'status':'completed'},{'checkpoint':None}]:
        assert not recoverable(dict(base,**change))

def test_monitor_counts_and_terminal_index():
    job={'id':'scan','status':'completed','checkpoint':{'hospital_index':2},'hospitals':[{'name':'One'},{'name':'Two'}],'pages':[{'status':'read'},{'status':'blocked'}],'results':[{}]}
    state=snapshot(job)
    assert state['centers_processed']==state['centers_total']==2
    assert state['current_center']=='' and state['page_statuses']=={'read':1,'blocked':1}
    assert state['price_findings']==1


def test_completed_scan_writes_report_and_exits(tmp_path,monkeypatch):
    import json
    import scan_monitor
    monkeypatch.setattr(scan_monitor,'DATA',tmp_path)
    key='a'*32
    (tmp_path/(key+'.json')).write_text(json.dumps({'id':key,'status':'completed','enrichment_status':'ready','hospitals':[{'name':'One'}],'checkpoint':{'hospital_index':1}}),encoding='utf-8')
    scan_monitor.monitor(key,interval=0)
    output=tmp_path/'monitoring'/key
    assert 'Status: completed' in (output/'completion.txt').read_text(encoding='utf-8')
    assert json.loads((output/'status.json').read_text(encoding='utf-8'))['monitor_finished_at']


def test_crashed_server_restarts_then_resumes_saved_checkpoint(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    import scan_monitor
    monkeypatch.setattr(scan_monitor,'DATA',tmp_path)
    key='b'*32;path=tmp_path/(key+'.json')
    job={'id':key,'status':'running','hospitals':[{'name':'One'}],'checkpoint':{'hospital_index':0},'pause_requested':False}
    def save():path.write_text(json.dumps(job),encoding='utf-8')
    save();calls=[]
    def restart():
        calls.append('restart');job['status']='paused';save();return SimpleNamespace(pid=123)
    def resume(client,scan_id):
        assert scan_id==key and job['status']=='paused'
        calls.append('resume');job.update(status='completed',enrichment_status='ready');save()
    monkeypatch.setattr(scan_monitor,'connection_refused',lambda:True)
    monkeypatch.setattr(scan_monitor,'start_server',restart)
    monkeypatch.setattr(scan_monitor,'resume_after_crash',resume)
    monkeypatch.setattr(scan_monitor.time,'sleep',lambda _:None)
    scan_monitor.monitor(key,interval=0)
    assert calls==['restart','resume']
    assert json.loads((tmp_path/'monitoring'/key/'status.json').read_text(encoding='utf-8'))['recovery_attempts']==1
