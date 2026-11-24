import asyncio
import pytest
from fastapi.testclient import TestClient
import app
from parallel_crawler import run, migrate
from test_parallel_crawler import job_for


def test_concurrency_api_validates_and_persists_without_ceiling(monkeypatch):
    job=job_for(['a.example']);job['status']='running';migrate(job)
    saved=[]
    monkeypatch.setattr(app,'JOBS',{'parallel':job})
    monkeypatch.setattr(app,'save',lambda j:saved.append(j['concurrency']))
    client=TestClient(app.app);headers={'x-app-token':app.TOKEN}
    for value in [1,8,1000000]:
        response=client.post('/api/scans/parallel/concurrency',json={'concurrency':value},headers=headers)
        assert response.status_code==200 and job['concurrency']==value
    for value in [0,-1,1.5,True,'2',None]:
        assert client.post('/api/scans/parallel/concurrency',json={'concurrency':value},headers=headers).status_code==422
    assert saved==[1,8,1000000]
    assert client.post('/api/scans/parallel/concurrency',json={'concurrency':2}).status_code==403
    job['status']='completed'
    assert client.post('/api/scans/parallel/concurrency',json={'concurrency':2},headers=headers).status_code==409
    assert app.ScanInput(concurrency=1000000).concurrency==1000000


def test_live_increase_and_decrease_drain_workers_without_loss():
    async def scenario():
        job=job_for([f'{i}.example' for i in range(9)]);job['concurrency']=1
        active=set();began=[];release=asyncio.Event()
        async def serial(child,save,limits):
            i=child['hospitals'][0]['record_id'];active.add(i);began.append(i)
            child['status']='running';save(child)
            await release.wait()
            await asyncio.sleep(.01)
            child['results'].append({'id':i});child['status']='completed';active.remove(i);save(child)
        task=asyncio.create_task(run(job,lambda _:None,serial))
        async def until(predicate):
            async with asyncio.timeout(3):
                while not predicate():await asyncio.sleep(.01)
        await until(lambda:len(active)==1)
        job['concurrency']=6
        await until(lambda:len(active)==6)
        job['concurrency']=2
        await asyncio.sleep(.25)
        assert len(active)==6 and len(began)==6
        release.set()
        await task
        assert job['completed_centers']==9 and len({r['id'] for r in job['results']})==9
        assert job['concurrency']==2
    asyncio.run(scenario())
