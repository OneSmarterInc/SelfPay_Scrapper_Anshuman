import asyncio,json
import crawler

CONTACT='<script type="application/ld+json">{"@type":"MedicalClinic","name":"Clinic","address":{"streetAddress":"12 Main St","addressLocality":"Reno","addressRegion":"NV","postalCode":"89511"},"telephone":"775-555-0100"}</script>'
PRICE='<h1>Self-pay</h1><p>MRI without contrast $399</p>'

def run(monkeypatch,pages,*,limit=500,pause=False,saved_links=False,second_center=False):
    calls=[]
    job={'hospitals':[{'name':'First','url':'https://example.com/','seed_urls':['https://example.com/contact','https://example.com/pricing'] if saved_links else []}],'max_pages':limit,'max_depth':None,'pages':[],'results':[],'warnings':[]}
    if second_center: job['hospitals'].append({'name':'Second','url':'https://example.com/'})
    async def fetch(client,url,seed):
        calls.append(url)
        path=url.split('.com')[-1]
        if path=='/robots.txt':return url,'text/plain',b'User-agent: *\nAllow: /'
        if path=='/sitemap.xml':return url,'text/xml',('<urlset>'+''.join('<url><loc>https://example.com'+p+'</loc></url>' for p in pages if p!='/')+'</urlset>').encode()
        if path=='/cms-hpt.txt':return url,'text/plain',b''
        if pause and path=='/':job['pause_requested']=True
        return url,'text/html',('<html>'+pages.get(path,'Other page')+'</html>').encode()
    async def sleep(_):pass
    monkeypatch.setattr(crawler,'fetch',fetch);monkeypatch.setattr(crawler.asyncio,'sleep',sleep)
    asyncio.run(crawler.crawl(job,lambda _:None))
    return job,calls


def test_sitemap_url_promoted_by_price_link_and_preserved_on_resume(monkeypatch):
    pages={'/':PRICE+CONTACT+'<a href="/details-123">Cash pricing</a>','/details-123':PRICE,'/news':'irrelevant'}
    job,calls=run(monkeypatch,pages,pause=True)
    assert job['status']=='paused'
    saved=json.loads(json.dumps(job));saved['pause_requested']=False
    asyncio.run(crawler.crawl(saved,lambda _:None))
    assert calls.count('https://example.com/details-123')==2
    assert 'https://example.com/news' not in calls
    assert saved['hospitals'][0]['stop_reason']=='Required details found'


def test_contact_followup_is_bounded_when_price_found(monkeypatch):
    pages={'/':PRICE,**{f'/contact/{i}':'No contacts published' for i in range(25)},'/news':'irrelevant'}
    job,calls=run(monkeypatch,pages)
    assert len([url for url in calls if '/contact/' in url])==10
    assert 'https://example.com/news' not in calls
    assert job['hospitals'][0]['stop_reason']=='Pricing checked; contact search finished'


def test_without_prices_scan_reaches_budget(monkeypatch):
    pages={'/':CONTACT,**{f'/page/{i}':'No price' for i in range(30)}}
    job,calls=run(monkeypatch,pages,limit=12)
    assert job['hospitals'][0]['attempted_pages']==12
    assert job['hospitals'][0]['stop_reason']=='Page limit reached'
    assert not job['hospitals'][0]['required_details']['found']['price']


def test_foreign_currency_does_not_satisfy_us_price_goal():
    hospital={'name':'A','url':'https://example.com/'}
    row={'hospital':'A','source_url':hospital['url'],'price_type':'self-pay / cash','currency':'CAD','verification_status':'repeated source match'}
    assert not crawler.required_details({'results':[row]},hospital)['found']['price']


def test_saved_contact_url_does_not_jump_ahead_of_pricing(monkeypatch):
    job,calls=run(monkeypatch,{'/':'Home','/contact':CONTACT,'/pricing':PRICE,'/news':'Other'},saved_links=True)
    assert calls.index('https://example.com/pricing')<calls.index('https://example.com/contact')


def test_discovery_fetch_has_short_timeout_without_retries(monkeypatch):
    attempts=[]
    async def once(client,url,seed):
        raise AssertionError('The fake timeout must intercept this request')
    async def timeout(awaitable,timeout):
        attempts.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError()
    monkeypatch.setattr(crawler,'_fetch_once',once)
    monkeypatch.setattr(crawler.asyncio,'wait_for',timeout)
    import pytest
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(crawler.fetch(None,'https://example.com/cms-hpt.txt','https://example.com/'))
    assert attempts==[10]


def test_regular_fetch_timeout_retries_are_bounded(monkeypatch):
    attempts=[]
    async def once(client,url,seed):
        raise AssertionError('The fake timeout must intercept this request')
    async def timeout(awaitable,timeout):
        attempts.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError()
    async def sleep(_):pass
    monkeypatch.setattr(crawler,'_fetch_once',once)
    monkeypatch.setattr(crawler.asyncio,'wait_for',timeout)
    monkeypatch.setattr(crawler.asyncio,'sleep',sleep)
    import pytest
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(crawler.fetch(None,'https://example.com/pricing','https://example.com/'))
    assert attempts==[180,180,180]


def test_unlimited_pages_exhaust_routes_and_move_to_next_center(monkeypatch):
    pages={'/':'No price',**{f'/page/{i}':'No price' for i in range(510)}}
    job,calls=run(monkeypatch,pages,limit=None,second_center=True)
    assert job['status']=='completed'
    assert job['hospitals'][0]['attempted_pages']>500
    assert job['hospitals'][0]['stop_reason']=='Available routes checked'
    assert job['hospitals'][0]['remaining_routes']==0
    assert not job['hospitals'][0]['required_details']['found']['price']

    assert job['hospitals'][1]['stop_reason']=='Available routes checked'
    assert job['hospitals'][1]['attempted_pages']>500
    assert [p['hospital'] for p in job['pages'] if p.get('hospital')][-1]=='Second'
