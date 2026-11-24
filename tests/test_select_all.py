import asyncio
import app

def test_directory_scan_supports_over_twenty_and_shared_websites(monkeypatch):
    records=[{'id':str(i),'name':f'Center {i}','urls':['https://example.com'],'source':{}} for i in range(35)]
    monkeypatch.setattr(app,'directory',lambda:{'records':records})
    monkeypatch.setattr(app,'JOBS',{})
    monkeypatch.setattr(app,'TASKS',set())
    monkeypatch.setattr(app,'save',lambda job:None)
    async def fake_run(job):pass
    monkeypatch.setattr(app,'run_scan',fake_run)
    result=asyncio.run(app.start_scan(app.ScanInput(record_ids=[r['id'] for r in records])))
    assert len(app.JOBS[result['id']]['hospitals'])==35


def test_mixed_scan_keeps_twenty_provider_limit(monkeypatch):
    import pytest
    records=[{'id':str(i),'name':f'Center {i}','urls':[f'https://provider{i}.example.com'],'source':{}} for i in range(21)]
    monkeypatch.setattr(app,'directory',lambda:{'records':records})
    monkeypatch.setattr(app,'JOBS',{})
    with pytest.raises(app.HTTPException):
        asyncio.run(app.start_scan(app.ScanInput(record_ids=[r['id'] for r in records],urls=['https://other.example.com'])))
