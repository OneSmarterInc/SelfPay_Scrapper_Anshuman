import asyncio
import json
import crawler


def test_pause_resume_persisted_frontier(monkeypatch):
    calls=[]
    job={'status':'queued','hospitals':[{'name':'Test','url':'https://example.com/'}], 'max_pages':10,'max_depth':3,'pages':[],'results':[],'warnings':[]}
    async def fetch(client,url,seed):
        calls.append(url)
        if url.endswith('robots.txt'): return url,'text/plain',b'User-agent: *\nAllow: /'
        if url.endswith('sitemap.xml'): return url,'text/xml',b'<urlset/>'
        if url.endswith('cms-hpt.txt'): return url,'text/plain',b''
        if url == 'https://example.com/':
            job['pause_requested']=True
            return url,'text/html',b'<html><a href="/pricing">Pricing</a></html>'
        return url,'text/html',b'<html><h1>Self-pay MRI</h1><p>MRI without contrast $499</p></html>'
    async def sleep(_): pass
    monkeypatch.setattr(crawler,'fetch',fetch)
    monkeypatch.setattr(crawler.asyncio,'sleep',sleep)
    asyncio.run(crawler.crawl(job,lambda _:None))
    assert job['status']=='paused'
    assert job['checkpoint']['queue']
    assert job['active_seconds']>=0
    elapsed=job['active_seconds']
    saved=json.loads(json.dumps(job))
    saved['pause_requested']=False
    asyncio.run(crawler.crawl(saved,lambda _:None))
    assert saved['status']=='completed'
    assert saved['active_seconds']>=elapsed
    assert saved['started_at']==job['started_at']
    assert calls.count('https://example.com/')==1
    assert calls.count('https://example.com/sitemap.xml')==1
    assert len(saved['results'])==1
    assert saved['results'][0]['crawl_route'][-1]['url']=='https://example.com/pricing'


def test_cancelled_request_resumes_unfinished_page(monkeypatch):
    job={'status':'queued','hospitals':[{'name':'Test','url':'https://example.com/'}], 'max_pages':5,'max_depth':1,'pages':[],'results':[],'warnings':[]}
    crash=True
    async def fetch(client,url,seed):
        nonlocal crash
        if url.endswith('robots.txt'): return url,'text/plain',b'User-agent: *\nAllow: /'
        if url.endswith('sitemap.xml'): return url,'text/xml',b'<urlset/>'
        if url.endswith('cms-hpt.txt'): return url,'text/plain',b''
        if crash:
            crash=False
            raise asyncio.CancelledError()
        return url,'text/html',b'<html><h1>Self-pay MRI</h1><p>MRI without contrast $499</p></html>'
    async def sleep(_): pass
    monkeypatch.setattr(crawler,'fetch',fetch);monkeypatch.setattr(crawler.asyncio,'sleep',sleep)
    try: asyncio.run(crawler.crawl(job,lambda _:None))
    except asyncio.CancelledError: pass
    assert job['status']=='paused'
    restored=json.loads(json.dumps(job))
    asyncio.run(crawler.crawl(restored,lambda _:None))
    assert restored['status']=='completed'
    assert len(restored['results'])==1
    assert not any(p['status']=='reading' for p in restored['pages'])
    assert len([p for p in restored['pages'] if p['url']=='https://example.com/'])==1

def test_restart_and_resume_api(tmp_path,monkeypatch):
    import app
    from fastapi.testclient import TestClient
    job={'id':'saved','status':'running','pages':[],'results':[],'contacts':[],'entities':[],'warnings':[], 'hospitals':[], 'cancel_requested':False}
    crawler.checkpoint(job,0)
    (tmp_path/'saved.json').write_text(json.dumps(job))
    monkeypatch.setattr(app,'DATA',tmp_path);monkeypatch.setattr(app,'JOBS',{});monkeypatch.setattr(app,'TASKS',set())
    async def fake_run(job):
        await asyncio.sleep(100)
    monkeypatch.setattr(app,'run_scan',fake_run)
    with TestClient(app.app) as client:
        headers={'x-app-token':client.get('/api/session').json()['token']}
        assert client.get('/api/scans/saved').json()['status']=='paused'
        assert 'checkpoint' not in client.get('/api/scans/saved').json()
        assert client.post('/api/scans/saved/resume',headers=headers).status_code==200
        assert client.post('/api/scans/saved/resume',headers=headers).status_code==409
        assert client.post('/api/scans/saved/pause',headers=headers).json()['pause_requested']


def test_clock_excludes_time_between_runs():
    job={'status':'queued','hospitals':[], 'pages':[],'results':[],'warnings':[], 'active_seconds':45,'timer_updated_at':'2000-01-01T00:00:00+00:00'}
    asyncio.run(crawler.crawl(job,lambda _:None))
    assert 45<=job['active_seconds']<50
    assert job['status']=='completed'
