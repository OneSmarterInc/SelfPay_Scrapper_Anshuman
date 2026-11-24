import asyncio
import pytest
import crawler
from app import ScanInput


def test_default_has_no_depth_limit():
    assert ScanInput().max_depth is None


@pytest.mark.parametrize('depth,expect_price',[(None,True),(6,False)])
def test_deep_pricing_routes(monkeypatch,depth,expect_price):
    job={'hospitals':[{'name':'Deep Center','url':'https://example.com/'}],'max_pages':30,'max_depth':depth,'pages':[],'results':[],'warnings':[]}
    async def fetch(client,url,seed):
        if url.endswith('robots.txt'):return url,'text/plain',b'User-agent: *\nAllow: /'
        if url.endswith('sitemap.xml'):return url,'text/xml',b'<urlset/>'
        if url.endswith('cms-hpt.txt'):return url,'text/plain',b''
        path=url.split('.com')[1].strip('/')
        level=int(path.removeprefix('level')) if path else 0
        html='<html><h1>Self-pay</h1><p>MRI without contrast $399</p></html>' if level==10 else f'<html><a href="/level{level+1}">Next page</a></html>'
        return url,'text/html',html.encode()
    async def sleep(_):pass
    monkeypatch.setattr(crawler,'fetch',fetch);monkeypatch.setattr(crawler.asyncio,'sleep',sleep)
    asyncio.run(crawler.crawl(job,lambda _:None))
    assert bool(job['results'])==expect_price
    assert job['hospitals'][0]['attempted_pages']<=30
    if expect_price:assert job['results'][0]['source_url'].endswith('/level10')
