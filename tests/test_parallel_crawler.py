import asyncio
import copy
import json
from collections import Counter
import crawler
from parallel_crawler import run,migrate,host


def job_for(hosts):
    return {'id':'parallel','concurrency':4,'status':'queued','hospitals':[{'name':f'Center {i}','url':'https://'+h+'/', 'record_id':str(i)} for i,h in enumerate(hosts)],'pages':[],'results':[],'contacts':[],'entities':[],'warnings':[],'max_pages':None,'max_depth':None}


def test_four_centers_overlap_and_same_host_never_overlaps():
    job=job_for(['a.example','a.example','b.example','c.example','d.example','e.example'])
    active=set();peak=0;seen=[]
    async def serial(child,save,limits):
        nonlocal peak
        site=host(child['hospitals'][0]);assert site not in active
        active.add(site);peak=max(peak,len(active));seen.append(child['hospitals'][0]['record_id'])
        child['status']='running';save(child)
        await asyncio.sleep(.03)
        child['results'].append({'id':child['hospitals'][0]['record_id']})
        child['status']='completed';save(child);active.remove(site)
    asyncio.run(run(job,lambda _:None,serial))
    assert peak==4 and sorted(seen)==[str(i) for i in range(6)]
    assert job['completed_centers']==6 and job['status']=='completed'
    assert len({r['id'] for r in job['results']})==6
    assert job['checkpoint']['workers']=={}


def test_parallel_pause_json_resume_keeps_each_frontier_and_findings():
    job=job_for(['a.example','b.example','c.example','d.example','e.example'])
    began=[]
    async def serial(child,save,limits):
        index=child['hospitals'][0]['record_id'];began.append(index)
        child['status']='running'
        if not child.get('checkpoint'):
            child['results'].append({'id':index})
            child['checkpoint']={'version':1,'hospital_index':0,'queue':[[0,1,'https://example/pricing']], 'lengths':{'results':1}}
        save(child)
        await asyncio.sleep(.02)
        if not job.get('pause_requested'):job['pause_requested']=True
        child['status']='paused';save(child)
    asyncio.run(run(job,lambda _:None,serial))
    assert job['status']=='paused' and len(job['checkpoint']['workers'])==4
    assert len(job['results'])==4 and '4' not in began
    restored=json.loads(json.dumps(job));restored['pause_requested']=False
    async def resume(child,save,limits):
        index=child['hospitals'][0]['record_id']
        if index!='4':assert child['checkpoint']['queue'] and child['results'][0]['id']==index
        else:child['results'].append({'id':index})
        child['status']='completed';save(child)
    asyncio.run(run(restored,lambda _:None,resume))
    assert restored['status']=='completed' and len(restored['results'])==5
    assert {r['id'] for r in restored['results']}==set('01234')


def test_migration_preserves_existing_rows_and_translates_local_checkpoint():
    job=job_for(['done.example','current.example','next.example'])
    job['results']=[{'id':'done','hospital':'Center 0','record_id':'0'},{'id':'current','hospital':'Center 1','record_id':'1'}]
    job['pages']=[{'hospital':'Center 0','provider_index':0,'url':'done'},{'hospital':'Center 1','provider_index':1,'url':'current'}]
    job['contacts']=[{'hospital':'Center 1','record_id':'1','value':'555'}]
    crawler.checkpoint(job,1,[(0,2,'https://current.example/pricing')],{'https://current.example/pricing'},{'https://current.example/'},{},9)
    before=copy.deepcopy(job)
    migrate(job)
    child=job['checkpoint']['workers']['1']
    assert job['completed_centers']==1 and job['checkpoint']['pending_indices']==[2]
    assert child['checkpoint']['hospital_index']==0 and child['checkpoint']['attempted']==9
    assert child['checkpoint']['lengths']['results']==1 and child['checkpoint']['lengths']['pages']==1
    assert child['pages'][0]['provider_index']==0
    assert [r['id'] for r in job['results']]==[r['id'] for r in before['results']]
    assert len(job['pages'])==2 and len(job['contacts'])==1
    json.dumps(job)


def test_real_crawler_migration_resumes_current_route_without_repeating_completed_center(monkeypatch):
    job=job_for(['done.example','current.example','next.example'])
    job['results']=[{'id':'saved','hospital':'Center 0','record_id':'0','source_url':'https://done.example/pricing','review':'unreviewed'}]
    crawler.checkpoint(job,1,[(0,1,'https://current.example/pricing')],{'https://current.example/pricing'},{'https://current.example/'},{},1)
    calls=[]
    async def fetch(client,url,seed):
        calls.append(url)
        if url.endswith('robots.txt'):return url,'text/plain',b'User-agent: *\nAllow: /'
        if url.endswith('sitemap.xml'):return url,'text/xml',b'<urlset/>'
        if url.endswith('cms-hpt.txt'):return url,'text/plain',b''
        return url,'text/html',b'<h1>Self-pay</h1><p>MRI without contrast $399</p>'
    monkeypatch.setattr(crawler,'fetch',fetch)
    asyncio.run(crawler.crawl(job,lambda _:None))
    assert job['status']=='completed' and job['completed_centers']==3
    assert not any('done.example' in url for url in calls)
    assert 'https://current.example/' not in calls
    assert any(r['id']=='saved' for r in job['results'])
    assert any(r.get('record_id')=='1' and r.get('amount')=='399' for r in job['results'])
    assert all(p.get('provider_index') in (1,2) for p in job['pages'])


def test_browser_and_pdf_limits_bound_operations():
    async def exercise(limit):
        active=0;peak=0
        async def work():
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            await asyncio.sleep(.01);active-=1
        semaphore=asyncio.Semaphore(limit)
        await asyncio.gather(*(crawler.limited(semaphore,work) for _ in range(8)))
        return peak
    assert asyncio.run(exercise(2))==2
    assert asyncio.run(exercise(1))==1


def test_crash_after_out_of_order_completion_resumes_only_unfinished_centers(monkeypatch):
    job=job_for(['slow.example','fast.example','third.example','fourth.example'])
    calls=[]
    async def scenario():
        fast_done=asyncio.Event();blocked=asyncio.Event()
        async def fetch(client,url,seed):
            calls.append(url)
            if url.endswith('robots.txt'):return url,'text/plain',b'User-agent: *\nAllow: /'
            if url.endswith('sitemap.xml'):return url,'text/xml',b'<urlset/>'
            if url.endswith('cms-hpt.txt'):return url,'text/plain',b''
            if 'slow.example' in url:await blocked.wait()
            return url,'text/html',b'<h1>Self-pay</h1><p>MRI without contrast $399</p>'
        monkeypatch.setattr(crawler,'fetch',fetch)
        def save(current):
            if 1 in current['checkpoint'].get('completed_indices',[]):fast_done.set()
        task=asyncio.create_task(crawler.crawl(job,save))
        await asyncio.wait_for(fast_done.wait(),10)
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        assert job['status']=='paused' and 1 in job['checkpoint']['completed_indices']
        saved=json.loads(json.dumps(job));before=len(calls)
        saved['pause_requested']=False;blocked.set()
        await crawler.crawl(saved,lambda _:None)
        assert saved['status']=='completed' and saved['completed_centers']==4
        assert not any('fast.example' in url for url in calls[before:])
        assert len(saved['results'])==4 and len({r['id'] for r in saved['results']})==4
        assert not any(p['status']=='reading' for p in saved['pages'])
    asyncio.run(scenario())


def test_cancel_does_not_start_queued_centers():
    job=job_for([f'{i}.example' for i in range(8)]);started=[]
    async def serial(child,save,limits):
        started.append(child['hospitals'][0]['record_id']);child['status']='running';save(child)
        if len(started)==4:job['cancel_requested']=True
        while not child.get('cancel_requested'):await asyncio.sleep(.01)
        child['status']='cancelled';save(child)
    asyncio.run(run(job,lambda _:None,serial))
    assert job['status']=='cancelled' and len(started)==4


def test_review_changes_survive_worker_saves_and_unfinished_page_replay():
    from parallel_crawler import merge,remember_review
    job=job_for(['a.example']);migrate(job)
    child={'pages':[],'results':[{'id':'p','review':'unreviewed'}],'contacts':[],'entities':[],'warnings':[],'hospitals':job['hospitals']}
    merge(job,0,child)
    parent=job['results'][0];parent.update(review='rejected',reviewed_at='2026-11-24T00:00:00Z');remember_review(job,'results',parent)
    merge(job,0,child)
    assert child['results'][0]['review']=='rejected' and job['results'][0]['review']=='rejected'
    child['results']=[];merge(job,0,child)
    child['results']=[{'id':'p','review':'unreviewed'}];merge(job,0,child)
    assert job['results'][0]['review']=='rejected'
