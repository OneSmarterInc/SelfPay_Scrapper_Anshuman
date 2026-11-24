import csv
import io
import json
from pathlib import Path
import pytest
from directory import parse_directory
from enrichment import Store
from test_enrichment import seed,contact


def test_import_missing_ids_are_exportable_and_not_row_dependent():
    a=parse_directory('Center or operator,Stable source record ID\nAlpha,\nBeta,')
    b=parse_directory('Center or operator,Stable source record ID\nBeta,\nAlpha,')
    assert {r['name']:r['id'] for r in a['records']}=={r['name']:r['id'] for r in b['records']}
    assert all(r['id']==r['source']['Stable source record ID'] for r in a['records'])


@pytest.mark.parametrize('text',[
    'Center or operator,Stable source record ID,State,State\nA,D-1,AZ,NY',
    'Center or operator,Stable source record ID\nA,D-1,extra',
    'Center or operator,Stable source record ID\n"A,D-1',
])
def test_malformed_import_rejected_before_data_loss(text):
    with pytest.raises(ValueError):parse_directory(text)


def test_missing_trailing_columns_preserved():
    data=parse_directory('Center or operator,Stable source record ID,State\nA,D-1')
    assert data['records'][0]['source']['State']==''


def test_update_preserves_directory_order_and_route(tmp_path):
    store=seed(tmp_path)
    store.import_data(parse_directory('Center or operator,Stable source record ID\nSecond,D-2'))
    before=[r['id'] for r in store.directory()['records']]
    proposal=contact();proposal['crawl_route']=[{'url':'https://example.com','via':'Submitted source URL'},{'url':proposal['source_url'],'via':'Internal link'}]
    proposal['content_sha256']='abc123'
    store.add_proposals([proposal]);store.review([proposal['id']],'approved');store.apply([proposal['id']])
    after=store.directory()['records'];assert [r['id'] for r in after]==before
    provenance=json.loads(after[0]['source']['Enrichment provenance JSON'])
    assert provenance[0]['crawl_route']==proposal['crawl_route']
    assert provenance[0]['content_sha256']=='abc123'


def test_invalidated_count_and_uncached_api(tmp_path,monkeypatch):
    import app
    from fastapi.testclient import TestClient
    store=seed(tmp_path);p=contact();p.update(scan_id='s',source_result_ids=['r']);store.add_proposals([p]);store.reject_source('s','r')
    monkeypatch.setattr(app,'DATA',tmp_path);monkeypatch.setattr(app,'JOBS',{})
    with TestClient(app.app) as client:
        response=client.get('/api/enrichment')
        assert response.json()['counts']['invalidated']==1
        assert response.headers['cache-control']=='no-store'
        assert client.get('/api/session').headers['cache-control']=='no-store'
