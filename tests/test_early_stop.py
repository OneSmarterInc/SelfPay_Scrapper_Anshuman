import asyncio
import crawler


def test_route_order():
    paths=['/news','/services','/about','/contact','/pricing']
    assert sorted(paths,key=lambda p:crawler.route_priority('https://example.com'+p))==list(reversed(paths))


def test_stop_after_required_details_moves_to_next_center(monkeypatch):
    calls=[]
    job={'hospitals':[{'name':'First','url':'https://one.example.com/'},{'name':'Second','url':'https://two.example.com/'}],'max_pages':500,'max_depth':4,'pages':[],'results':[],'warnings':[]}
    async def fetch(client,url,seed):
        calls.append(url)
        origin='https://'+url.split('/')[2]
        if url.endswith('robots.txt'):return url,'text/plain',b'User-agent: *\nAllow: /'
        if url.endswith('sitemap.xml'):return url,'text/xml',('<urlset>'+''.join('<url><loc>'+origin+p+'</loc></url>' for p in ['/news','/about','/pricing','/contact','/cash-prices'])+'</urlset>').encode()
        if url.endswith('/contact'):return url,'text/html',b'<html><script type="application/ld+json">{"@type":"MedicalClinic","name":"Imaging","address":{"streetAddress":"12 Main St","addressLocality":"Reno","addressRegion":"NV","postalCode":"89511"},"telephone":"775-555-0100"}</script><p>Contact our imaging center for appointments and directions.</p></html>'
        if url.endswith(('/pricing','/cash-prices')):return url,'text/html',b'<html><h1>Self-pay</h1><p>MRI without contrast $399</p></html>'
        return url,'text/html',b'<html><p>Other page</p></html>'
    async def sleep(_):pass
    monkeypatch.setattr(crawler,'fetch',fetch);monkeypatch.setattr(crawler.asyncio,'sleep',sleep)
    asyncio.run(crawler.crawl(job,lambda _:None))
    assert job['status']=='completed'
    assert all(h['stop_reason']=='Required details found' for h in job['hospitals'])
    assert all(h['remaining_routes']>0 for h in job['hospitals'])
    assert not any(url.endswith(('/about','/news')) for url in calls)
    assert calls.index('https://one.example.com/pricing')<calls.index('https://one.example.com/contact')<calls.index('https://two.example.com/pricing')
    assert calls.count('https://one.example.com/pricing')==2
    assert calls.count('https://one.example.com/cash-prices')==2
    assert not any('page limit' in w for w in job['warnings'])


def test_missing_or_unverified_data_does_not_complete():
    hospital={'name':'Center','url':'https://example.com','record_id':'A'}
    owner={'hospital':'Center','source_url':'https://example.com/contact','record_id':'A'}
    contacts=[dict(owner,field=f,value='published') for f in ['Current supported address','Current supported call number']]
    result=dict(owner,currency='USD',price_type='self-pay / cash',verification_status='repeated source match',extraction_issues=[])
    job={'contacts':contacts,'results':[result]}
    assert crawler.required_details(job,hospital)['complete']
    assert crawler.required_details(job,hospital)['optional_missing']==['email']
    contacts.append(dict(owner,field='Current usable email',value='info@example.com'))
    result['verification_status']='recheck failed'
    assert crawler.required_details(job,hospital)['missing']==['price']
    result['verification_status']='repeated source match';result['record_id']='B'
    assert not crawler.required_details(job,hospital)['complete']


def test_prices_remain_mandatory_and_pricing_queue_is_finished():
    found={'price':True,'phone':True,'address':True,'email':False}
    complete={'found':found,'complete':True}
    assert not crawler.early_stop_reason(complete,[(0,1,'https://example.com/cash')],99)
    assert crawler.early_stop_reason(complete,[(4,1,'https://example.com/blog')],0)=='Required details found'
    missing_contacts={'found':dict(found,phone=False,address=False),'complete':False}
    assert not crawler.early_stop_reason(missing_contacts,[(1,1,'https://example.com/contact')],9)
    assert crawler.early_stop_reason(missing_contacts,[(1,1,'https://example.com/contact')],10)
    assert crawler.early_stop_reason(missing_contacts,[(4,1,'https://example.com/blog')],0)
    missing_price={'found':dict(found,price=False),'complete':False}
    assert not crawler.early_stop_reason(missing_price,[(4,1,'https://example.com/blog')],99)
