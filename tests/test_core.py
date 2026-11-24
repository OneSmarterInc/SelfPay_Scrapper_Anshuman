import asyncio
import csv
import io
import json
from pathlib import Path
import pytest
import httpx
from extraction import extract_html, extract_text
from imaging import extract_mrf, enrich
from directory import parse_directory
import crawler


def test_prices_and_not_phones():
    rows=extract_html('<h1>Self-pay MRI</h1><p>MRI without contrast $499</p><p>Call 205-834-8118</p>', 'https://example.com')
    assert len(rows)==1
    result=enrich(rows[0])
    assert result['amount']=='499' and result['currency']=='USD'
    assert result['price_type']=='self-pay / cash'
    assert result['contrast']=='without contrast'


def test_do_not_invent_currency():
    assert extract_text('MRI scan 499', 'https://example.com')==[]


def test_table_price_column_not_code():
    html='<table><tr><th>Exam</th><th>CPT</th><th>Cash price USD</th></tr><tr><td>MRI brain</td><td>70551</td><td>499</td></tr></table>'
    rows=extract_html(html,'https://example.com')
    assert [r['amount'] for r in rows]==['499']


def test_multiple_prices_need_review():
    rows=extract_text('MRI was $800, now $499 self-pay', 'https://example.com')
    assert len(rows)==2 and all(r['confidence']=='needs review' for r in rows)


def test_ignore_deleted_old_price():
    rows=extract_html('<p>MRI self-pay <del>$800</del> $499</p>', 'https://example.com')
    assert [r['amount'] for r in rows]==['499']


def test_mrf_csv_only_cash():
    source='hospital_name,last_updated_on\nExample,2026-01-01\ndescription,code|1,code|1|type,standard_charge|gross,standard_charge|discounted_cash,setting\nMRI brain,70551,CPT,2000,499,outpatient\nOffice visit,99213,CPT,200,90,outpatient\nCT chest,71250,CPT,900,,outpatient\n'
    rows=extract_mrf(source.encode(), 'https://example.com/file.csv')
    assert len(rows)==1 and rows[0]['amount']=='499' and rows[0]['cpt']=='70551'


def test_mrf_json_only_cash():
    source={'hospital_name':'Test', 'standard_charge_information':[{'description':'MRI brain','code_information':[{'code':'70551','type':'CPT'}],'standard_charges':[{'gross_charge':2000,'discounted_cash':499,'setting':'outpatient','payers_information':[{'standard_charge_dollar':899}]}]}]}
    rows=extract_mrf(json.dumps(source).encode(),'https://example.com/file.json')
    assert len(rows)==1 and rows[0]['amount']=='499'


def test_non_cash_never_confirmed():
    row=enrich(extract_text('MRI gross charge $1200', 'https://example.com')[0])
    assert row['price_type']=='gross charge — not self-pay'


def test_imaging_filter():
    assert enrich(extract_text('Health check package $99', 'https://example.com')[0]) is None


def test_import_preamble_and_baseline():
    text='Unnamed: 0,Unnamed: 1\nTitle\n\nCenter or operator,Stable source record ID,Current supported offer / price scope,Official pricing / program source\nExample,D-1,Quote required,https://example.com\n'
    data=parse_directory(text)
    assert data['summary']['rows']==1 and data['summary']['header_row']==4
    assert data['records'][0]['baseline']=='Quote required'
    assert data['records'][0]['source']['Center or operator']=='Example'


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError,match='Duplicate'):
        parse_directory('Center or operator,Stable source record ID\nA,D-1\nB,D-1')


@pytest.mark.parametrize('url',['file:///etc/passwd','http://user:pass@example.com','http://example.com:8000'])
def test_invalid_urls(url):
    with pytest.raises(ValueError): crawler.canonical(url)


def test_private_urls():
    with pytest.raises(ValueError,match='public'):
        asyncio.run(crawler.public_url('http://127.0.0.1'))


def test_crawl_routes_and_evidence(monkeypatch):
    async def fake_public(url): return url
    async def fake_fetch(client,url,seed):
        pages={
            'https://example.com/robots.txt':('text/plain',b'User-agent: *\nDisallow: /private'),
            'https://example.com/sitemap.xml':('text/xml',b'<urlset><url><loc>https://example.com/pricing</loc></url></urlset>'),
            'https://example.com/':('text/html',b'<html><body><a href="/pricing">Self-pay pricing</a><a href="/private/contact">Contact</a></body></html>'),
            'https://example.com/pricing':('text/html',b'<html><h1>Self-pay prices</h1><p>MRI without contrast $499</p></html>'),
        }
        if url not in pages:
            raise ValueError('Missing fixture')
        content,raw=pages[url]
        return url,content,raw
    async def no_sleep(seconds): pass
    monkeypatch.setattr(crawler,'fetch',fake_fetch)
    monkeypatch.setattr(crawler,'public_url',fake_public)
    monkeypatch.setattr(crawler.asyncio,'sleep',no_sleep)
    job={'status':'queued','hospitals':[{'name':'Test','url':'https://example.com/','record_id':'D-1'}], 'baseline_records':[{'id':'D-1','baseline':'Old scope'}], 'max_pages':10,'max_depth':3,'pages':[],'results':[],'warnings':[]}
    asyncio.run(crawler.crawl(job,lambda _:None))
    assert job['status']=='completed' and len(job['results'])==1
    assert job['results'][0]['record_id']=='D-1'
    assert job['results'][0]['baseline_scope']=='Old scope'
    assert any(p['status']=='blocked' for p in job['pages'])


def test_api_review_and_csv_safety(tmp_path,monkeypatch):
    import app as module
    from fastapi.testclient import TestClient
    monkeypatch.setattr(module,'DATA',tmp_path)
    monkeypatch.setattr(module,'JOBS',{})
    with TestClient(module.app) as client:
        token=client.get('/api/session').json()['token']
        assert client.post('/api/scans',json={'urls':[]}).status_code==403
        assert client.post('/api/scans',json={'urls':[]},headers={'x-app-token':token}).status_code==400
        module.JOBS['test']={'id':'test','status':'completed','results':[{'id':'r','service':'=HYPERLINK("bad")','amount':'499','review':'unreviewed'}]}
        result=client.patch('/api/scans/test/results/r',json={'review':'accepted'},headers={'x-app-token':token})
        assert result.status_code==200 and result.json()['review']=='accepted'
        exported=client.get('/api/scans/test/export.csv?accepted_only=true')
        parsed=list(csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff'))))
        assert len(parsed)==1 and parsed[0]['service'].startswith("'=")
        assert (tmp_path/'test.json').exists()


def test_diagnostic_mammography_and_routine_offer():
    rows=extract_html('<h1>Self-Pay Pricing</h1><p>Diagnostic Mammogram bilateral $250</p><p>We offer MRI for $1000.</p>', 'https://example.com')
    assert [r['amount'] for r in rows]==['250','1000']
    assert rows[1]['qualifier']=='listed'


def test_cash_and_gross_columns_are_not_conflated():
    html='<h1>Self-pay pricing</h1><table><tr><th>Exam</th><th>Gross charge</th><th>Cash price</th></tr><tr><td>MRI brain</td><td>$2000</td><td>$499</td></tr></table>'
    rows=[enrich(r) for r in extract_html(html,'https://example.com')]
    assert len(rows)==2
    assert rows[0]['amount']=='2000' and rows[0]['price_type']=='gross charge — not self-pay'
    assert rows[1]['amount']=='499' and rows[1]['price_type']=='self-pay / cash'


def test_recheck_detects_changed_price_and_tracks_route(monkeypatch):
    calls={}
    async def fake_fetch(client,url,seed):
        calls[url]=calls.get(url,0)+1
        if url.endswith('robots.txt'): return url,'text/plain',b'User-agent: *\nDisallow:'
        if url.endswith('sitemap.xml'): return url,'text/xml',b'<urlset></urlset>'
        if url.endswith('/prices'):
            amount='499' if calls[url]==1 else '599'
            return url,'text/html',('<h1>Self-pay MRI</h1><p>MRI brain $'+amount+'</p>').encode()
        return url,'text/html',b'<html><a href="/prices">Pricing</a></html>'
    async def no_sleep(seconds): pass
    monkeypatch.setattr(crawler,'fetch',fake_fetch);monkeypatch.setattr(crawler.asyncio,'sleep',no_sleep)
    job={'id':'route-test','hospitals':[{'name':'Test','url':'https://example.com/','record_id':'D-1'}],'max_pages':3,'max_depth':2,'pages':[],'results':[],'warnings':[]}
    asyncio.run(crawler.crawl(job,lambda _:None))
    row=job['results'][0]
    assert row['verification_status']=='source changed'
    assert [step['url'] for step in row['crawl_route']]==['https://example.com/','https://example.com/prices']
    assert row['crawl_route'][-1]['via']=='Internal link: Pricing'


def test_cpt_first_table_preserves_description_and_code():
    html='<h1>Self-pay pricing</h1><table><tr><th>CPT</th><th>Description</th><th>Cash price</th></tr><tr><td>70551</td><td>MRI brain without contrast</td><td>$500</td></tr></table>'
    result=enrich(extract_html(html,'https://example.com')[0])
    assert result['cpt']=='70551'
    assert result['service'].startswith('MRI brain without contrast')
    assert result['amount']=='500' and result['price_type']=='self-pay / cash'


def test_deposit_is_ambiguous_and_negative_cash_not_confirmed():
    row=enrich(extract_text('Self-pay MRI deposit $50', 'https://example.com')[0])
    assert row['extraction_issues']
    row=enrich(extract_text('MRI $500. Cash not accepted.', 'https://example.com')[0])
    assert row['price_type']!='self-pay / cash'


def test_recheck_rejects_changed_heading(monkeypatch):
    count=0
    async def fake_fetch(client,url,seed):
        nonlocal count
        if url.endswith('robots.txt'): return url,'text/plain',b'User-agent: *\nDisallow:'
        if url.endswith('sitemap.xml'): return url,'text/xml',b'<urlset></urlset>'
        count+=1
        heading='Self-pay' if count==1 else 'Insurance rates'
        return url,'text/html',('<h2>'+heading+'</h2><p>MRI $500</p>').encode()
    async def no_sleep(seconds): pass
    monkeypatch.setattr(crawler,'fetch',fake_fetch);monkeypatch.setattr(crawler.asyncio,'sleep',no_sleep)
    job={'id':'context-test','hospitals':[{'name':'Test','url':'https://example.com/','record_id':'D-1'}],'max_pages':1,'max_depth':1,'pages':[],'results':[],'warnings':[]}
    asyncio.run(crawler.crawl(job,lambda _:None))
    assert job['results'][0]['verification_status']=='source changed'
