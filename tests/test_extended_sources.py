import asyncio,io
from PIL import Image,ImageDraw,ImageFont
from browser_render import render_sync,needs_render
from pdf_ocr import ocr_page
from extraction import extract_html
from imaging import enrich
import crawler


def test_javascript_price_and_route_render_locally():
    raw=b'''<html><body><div id="app"></div><script>
    setTimeout(()=>{document.getElementById('app').innerHTML='<h1>Self-pay cash prices USD</h1><p>MRI without contrast $399</p><a href="/pricing-details">Pricing details</a>';},200);
    fetch('http://127.0.0.1/api/directory').then(r=>r.text()).then(t=>document.body.dataset.privateData=t).catch(()=>{});
    </script></body></html>'''
    assert needs_render(raw,False,0)
    url,rendered,warnings=render_sync('https://example.com/pricing','https://example.com/',raw)
    rows=[enrich(r) for r in extract_html(rendered,url)]
    assert any(r and r['amount']=='399' and r['price_type']=='self-pay / cash' for r in rows)
    assert b'/pricing-details' in rendered
    assert b'data-private-data' not in rendered
    assert any('private' in w for w in warnings)


def test_structured_data_alone_does_not_launch_browser():
    assert not needs_render(b'<html><script type="application/ld+json">{}</script><p>Contact</p></html>',False,0)


def test_real_scanned_pdf_ocr_keeps_page_and_review_warning():
    image=Image.new('RGB',(1800,900),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',48)
    for y,text in [(70,'SELF-PAY CASH PRICES (USD)'),(200,'MRI without contrast $399'),(320,'CT without contrast $250')]:draw.text((80,y),text,font=font,fill='black')
    raw=io.BytesIO();image.save(raw,format='PDF',resolution=150)
    rows,chars=ocr_page(raw.getvalue(),1,'https://example.com/prices.pdf')
    assert {r['amount'] for r in rows}=={'399','250'}
    assert all(r['page']==1 and r['method']=='pdf OCR' and r['extraction_issues'] for r in rows)


def test_download_accepts_larger_file_and_rejects_over_cap(monkeypatch):
    import pytest
    class Response:
        is_redirect=False
        headers={'content-type':'application/pdf'}
        def raise_for_status(self):pass
        async def aiter_bytes(self):
            for _ in range(16):yield b'x'*(1024*1024)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    class Client:
        def stream(self,*args):return Response()
    async def public(url):return url
    monkeypatch.setattr(crawler,'public_url',public)
    assert len(asyncio.run(crawler._fetch_once(Client(),'https://example.com/file.pdf','https://example.com'))[2])==16*1024*1024
    monkeypatch.setattr(crawler,'MAX_BYTES',10*1024*1024)
    with pytest.raises(ValueError,match='100 MB'):asyncio.run(crawler._fetch_once(Client(),'https://example.com/file.pdf','https://example.com'))


def test_crawl_renders_and_rechecks_javascript_price(monkeypatch):
    from test_scan_flow_audit import run,CONTACT
    script='<div id="app"></div><script>document.getElementById("app").innerHTML="<h1>Self-pay cash prices USD</h1><p>MRI without contrast $399</p>";</script>'
    job,calls=run(monkeypatch,{'/':script+CONTACT,'/news':'Unneeded page'},limit=None)
    assert job['status']=='completed'
    assert any(r['amount']=='399' and r['verification_status']=='repeated source match' for r in job['results'])
    assert job['hospitals'][0]['stop_reason']=='Required details found'
    assert any('JavaScript-rendered' in p.get('detail','') for p in job['pages'])
