"""Local, isolated Chromium rendering for public provider pages."""
import asyncio
import time
import socket
import ipaddress
from urllib.parse import urljoin, urlsplit
from pathlib import Path
import os
import httpx

BROWSER_DIR=Path(__file__).parent/'data'/'browsers'
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(BROWSER_DIR))


def needs_render(raw,has_prices,priority):
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(raw,'html.parser')
    scripts=[tag for tag in soup.find_all('script') if tag.get('type','').lower() not in ('application/ld+json','application/json')]
    if not scripts:return False
    if any(soup.find(id=x) is not None for x in ('__next','root','app')):return True
    for node in soup(['script','style']):node.decompose()
    return len(soup.get_text(' ',strip=True))<180 or (not has_prices and priority<=3)


def render_sync(url,seed,raw):
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    from crawler import canonical, same_site, AGENT
    deadline=time.monotonic()+40
    transferred=0;requests=0;warnings=[]
    with sync_playwright() as pw, httpx.Client(timeout=10,trust_env=False) as client:
        browser=pw.chromium.launch(headless=True)
        try:
            context=browser.new_context(service_workers='block',accept_downloads=False,user_agent=AGENT)
            context.route_web_socket('**/*',lambda ws:ws.close())
            def intercept(route):
                nonlocal transferred,requests
                request=route.request;requests+=1
                try:
                    if time.monotonic()>deadline or requests>200:raise ValueError('Rendering resource budget reached')
                    if request.resource_type in ('image','media','font') or request.method not in ('GET','HEAD'):return route.abort()
                    target=request.url
                    if request.resource_type=='document' and not same_site(target,seed):return route.abort()
                    if target==url and request.resource_type=='document':
                        return route.fulfill(status=200,content_type='text/html',body=raw)
                    for _ in range(6):
                        target=canonical(target)
                        addresses=socket.getaddrinfo(urlsplit(target).hostname,None,0,socket.SOCK_STREAM)
                        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                            raise ValueError('Local/private browser resource blocked')
                        with client.stream('GET',target,headers={'User-Agent':AGENT}) as response:
                            if response.is_redirect:
                                target=urljoin(target,response.headers['location'])
                                if request.resource_type=='document' and not same_site(target,seed):raise ValueError('External frame redirect blocked')
                                continue
                            response.raise_for_status();body=bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk);transferred+=len(chunk)
                                if len(body)>20*1024*1024 or transferred>100*1024*1024 or time.monotonic()>deadline:raise ValueError('Rendering resource limit reached')
                            headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-encoding','content-length','transfer-encoding')}
                            return route.fulfill(status=response.status_code,body=bytes(body),headers=headers)
                    raise ValueError('Too many resource redirects')
                except Exception as exc:
                    warnings.append(str(exc));route.abort()
            context.route('**/*',intercept)
            page=context.new_page()
            page.goto(url,wait_until='domcontentloaded',timeout=45000)
            try:page.wait_for_load_state('networkidle',timeout=8000)
            except BrowserTimeout:pass
            page.wait_for_timeout(1200)
            # Trigger lazy content without clicking forms or consent controls.
            for _ in range(3):
                if time.monotonic()>deadline:break
                page.evaluate('window.scrollBy(0, window.innerHeight)');page.wait_for_timeout(400)
            result=page.content().encode('utf-8')
            if not same_site(page.url,seed):raise ValueError('External browser navigation skipped')
            if len(result)>20*1024*1024:raise ValueError('Rendered document exceeds 20 MB')
            return page.url,result,list(dict.fromkeys(warnings))[:5]
        finally:browser.close()


async def render_page(url,seed,raw):
    return await asyncio.to_thread(render_sync,url,seed,raw)
