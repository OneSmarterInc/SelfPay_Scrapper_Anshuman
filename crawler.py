from facility_matching import location_scope_issue
from pdf_prices import extract_pdf_page
import asyncio
import heapq
import io
import ipaddress
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
import re
import xml.etree.ElementTree as ET
import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from extraction import extract_html, extract_text
from imaging import enrich, extract_mrf
from contacts import extract_contacts
import hashlib

AGENT = "HospitalPriceResearch/0.1"
PRIORITY = re.compile(r"price|pricing|tariff|charge|cost|package|check.?up|health.?check|rate|billing|transparency|self.?pay|cash|standardcharges|contact|locations?|find.us|our.centers", re.I)
CONTACT_ROUTES=re.compile(r'contact|locations?|find[-_/ ]?us|our[-_/ ]?centers',re.I)
PRICE_ROUTES=re.compile(r'price|pricing|tariff|charge|cost|package|billing|transparency|self.?pay|cash|payment|financing|cms-hpt|screening|mri|ct[-_/ ]?scan|ultrasound|mammogra|x.?ray|check.?up|health.?check|specials?',re.I)
ABOUT_ROUTES=re.compile(r'about|who[-_/ ]?we[-_/ ]?are',re.I)
RELEVANT_ROUTES=re.compile(r'patient|services?|imaging|radiology|insurance|faq|appointment',re.I)

def route_priority(url,label=''):
    value=urlsplit(url).path+' '+label
    if PRICE_ROUTES.search(value):return 0
    if CONTACT_ROUTES.search(value):return 1
    if ABOUT_ROUTES.search(value):return 2
    if RELEVANT_ROUTES.search(value):return 3
    return 4


def required_details(job,hospital):
    rid=hospital.get('record_id') or ''
    baseline=next((r for r in job.get('baseline_records',[]) if r['id']==rid),{})
    def belongs(item):
        return (item.get('record_id') or '')==rid and item.get('hospital')==hospital['name'] and same_site(item.get('source_url',''),hospital['url']) and not location_scope_issue(baseline,item.get('source_url',''))
    contacts={c['field'] for c in job.get('contacts',[]) if belongs(c) and c.get('value') and c.get('review')!='rejected'}
    price=any(belongs(r) and r.get('price_type')=='self-pay / cash' and r.get('currency')=='USD' and not r.get('extraction_issues') and r.get('verification_status')=='repeated source match' and r.get('review')!='rejected' for r in job.get('results',[]))
    found={'price':price,'phone':'Current supported call number' in contacts,'address':'Current supported address' in contacts,'email':'Current usable email' in contacts}
    return {'found':found,'missing':[key for key in ('price','phone','address') if not found[key]],'optional_missing':[] if found['email'] else ['email'],'complete':all(found[key] for key in ('price','phone','address'))}

def early_stop_reason(coverage,queue,contact_pages):
    if not coverage['found']['price'] or any(priority==0 for priority,_,_ in queue):return ''
    if coverage['complete']:return 'Required details found'
    if contact_pages>=10 or not any(priority<=2 for priority,_,_ in queue):return 'Pricing checked; contact search finished'
    return ''

MAX_BYTES = 100 * 1024 * 1024


def canonical(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Enter a complete public http:// or https:// hospital URL.")
    if parts.port not in (None, 80, 443):
        raise ValueError("Only standard web ports are supported.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


async def public_url(url):
    url = canonical(url)
    host = urlsplit(url).hostname
    addresses = await asyncio.to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(addr[4][0]).is_global for addr in addresses):
        raise ValueError("Only public hospital websites can be scanned; local/private addresses are blocked.")
    return url


def same_site(url, seed):
    return (urlsplit(url).hostname or "").removeprefix("www.") == (urlsplit(seed).hostname or "").removeprefix("www.")


async def _fetch_once(client, url, seed):
    for _ in range(6):
        await public_url(url)
        if not same_site(url, seed):
            raise ValueError("External redirect skipped; add that website as a separate hospital.")
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                url = canonical(urljoin(url, response.headers["location"]))
                continue
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise ValueError("File exceeds the 100 MB scan limit.")
            return url, response.headers.get("content-type", ""), bytes(data)
    raise ValueError("Too many redirects.")


async def fetch(client, url, seed):
    discovery=urlsplit(url).path.endswith('/cms-hpt.txt')
    for attempt in range(1 if discovery else 3):
        try:
            return await asyncio.wait_for(_fetch_once(client, url, seed),timeout=10 if discovery else 180)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429,502,503,504) or discovery or attempt==2:
                raise
            retry=exc.response.headers.get('retry-after','')
            if retry and (not retry.isdigit() or int(retry)>60):
                raise
            await asyncio.sleep(max(2**attempt,int(retry or 0)))
        except (httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError,asyncio.TimeoutError):
            if discovery or attempt==2: raise
            await asyncio.sleep(2**attempt)


def checkpoint(job, hospital_index, queue=None, scheduled=None, visited=None, routes=None, attempted=0):
    job['checkpoint'] = {'version': 1, 'hospital_index': hospital_index,
        'queue': queue, 'scheduled': sorted(scheduled or []), 'visited': sorted(visited or []),
        'routes': routes or {}, 'attempted': attempted,
        'lengths': {key: len(job.get(key, [])) for key in ('pages','results','contacts','entities','warnings')}}
    # Copy mutable frontier before any in-flight work changes it.
    import copy
    job['checkpoint'] = copy.deepcopy(job['checkpoint'])


async def crawl(job, save):
    if job.get('concurrency',1)>1 or job.get('checkpoint',{}).get('version')==2:
        from parallel_crawler import run
        return await run(job,save,_crawl_serial)
    return await _crawl_serial(job,save)


async def limited(semaphore,operation,*args):
    async with semaphore:
        task=asyncio.create_task(operation(*args))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task,return_exceptions=True)
            raise


async def _crawl_serial(job, save, limits=None):
    limits=limits or {'browser':asyncio.Semaphore(2),'pdf':asyncio.Semaphore(1)}
    import time
    persist=save
    timer_baseline=float(job.get('active_seconds',0))
    timer_started=time.monotonic()
    def save(current):
        current['active_seconds']=timer_baseline+max(0,time.monotonic()-timer_started)
        current['timer_updated_at']=datetime.now(timezone.utc).isoformat()
        persist(current)
    job.setdefault('started_at',datetime.now(timezone.utc).isoformat())
    job["extraction_version"] = 7
    job.setdefault("contacts", [])
    job.setdefault("entities", [])
    cp = job.get('checkpoint')
    if cp:
        for key, length in cp['lengths'].items():
            del job[key][length:]
    else:
        checkpoint(job, 0)
    job.pop('finished_at', None)
    job["status"] = "running"
    save(job)
    try:
        async with httpx.AsyncClient(timeout=25, headers={"User-Agent": AGENT}, trust_env=False) as client:
            for hospital_index, hospital in enumerate(job["hospitals"]):
                if hospital_index < job['checkpoint']['hospital_index']:
                    continue
                if job.get('pause_requested'):
                    job['status'] = 'paused'
                    return
                if job.get("cancel_requested"):
                    break
                seed = hospital["url"]
                origin = urlunsplit((*urlsplit(seed)[:2], "", "", ""))
                robot = RobotFileParser()
                sitemaps = [origin + "/sitemap.xml"]
                try:
                    _, _, raw = await fetch(client, origin + "/robots.txt", seed)
                    rules = raw.decode("utf-8", "replace")
                    robot.parse(rules.splitlines())
                    sitemaps = (robot.site_maps() or []) + sitemaps
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in (404, 410):
                        job["pages"].append({"hospital": hospital["name"], "url": origin + "/robots.txt", "status": "blocked", "detail": "Robots rules unavailable; hospital skipped: " + str(exc)})
                        save(job)
                        continue
                    robot.parse([])
                except Exception as exc:
                    job["pages"].append({"hospital": hospital["name"], "url": seed, "status": "error", "detail": "Unable to check robots.txt: " + str(exc)})
                    save(job)
                    continue
                queue, scheduled, visited = [], set(), set()
                routes = {}
                def enqueue(url, depth, label="", parent=None, via="Internal link"):
                    try:
                        url = canonical(url)
                    except (ValueError, TypeError):
                        return
                    if not same_site(url, seed) or (job.get("max_depth") is not None and depth > job["max_depth"]):
                        return
                    if re.search(r"\.(?:jpg|jpeg|png|gif|svg|webp|zip|mp4|css|js|ico|woff2?)(?:\?|$)", url, re.I):
                        return
                    if url in scheduled:
                        # Later link text can identify a sitemap URL as a pricing page.
                        for index,(priority,old_depth,queued_url) in enumerate(queue):
                            if queued_url==url:
                                queue[index]=(min(priority,route_priority(url,label)),min(old_depth,depth),url)
                                heapq.heapify(queue)
                                break
                        return
                    scheduled.add(url)
                    routes[url] = list(routes.get(parent, [{"url":parent,"via":"Discovery source"}] if parent else [])) + [{"url":url,"via":via}]
                    heapq.heappush(queue, (route_priority(url,label), depth, url))
                enqueue(seed, 0, "pricing discovery" if urlsplit(seed).path in ("", "/") else "", via="Submitted source URL")
                enqueue(origin, 0, "pricing discovery", parent=seed, via="Website root derived from source")
                enqueue(origin + '/cms-hpt.txt', 0, parent=origin+'/', via='Standard transparency discovery file')
                for source in hospital.get('seed_urls', []):
                    enqueue(source, 0, via="Directory source URL")
                delay = max(0.6, min(float(robot.crawl_delay(AGENT) or 0.6), 60))
                if (robot.crawl_delay(AGENT) or 0) > 60:
                    job["pages"].append({"hospital": hospital["name"], "url": seed, "status": "blocked", "detail": "Requested crawl delay exceeds this app's 60-second limit; hospital skipped."})
                    continue
                seen_maps = set()
                if job['checkpoint']['hospital_index'] == hospital_index and job['checkpoint']['queue'] is not None:
                    sitemaps = []
                for sitemap in sitemaps:
                    if job.get("cancel_requested"):
                        break
                    if sitemap in seen_maps:
                        continue
                    seen_maps.add(sitemap)
                    try:
                        if not robot.can_fetch(AGENT, sitemap):
                            continue
                        await asyncio.sleep(delay)
                        _, _, raw = await fetch(client, sitemap, seed)
                        root = ET.fromstring(raw)
                        locations = [el.text for el in root.iter() if el.tag.endswith("}loc") or el.tag == "loc"]
                        if root.tag.endswith("sitemapindex"):
                            sitemaps.extend(url for url in locations if url and same_site(url,seed))
                        else:
                            for url in locations:
                                if url:
                                    enqueue(url, 1, parent=sitemap, via="Sitemap entry")
                    except Exception as exc:
                        job["pages"].append({"hospital": hospital["name"], "url": sitemap, "status": "discovery warning", "detail": str(exc)})
                attempted = 0
                cp = job['checkpoint']
                if cp['hospital_index'] == hospital_index and cp['queue'] is not None:
                    queue = [(min(item[0],route_priority(item[2],(cp['routes'].get(item[2]) or [{}])[-1].get('via',''))),item[1],item[2]) for item in cp['queue']]
                    heapq.heapify(queue)
                    scheduled, visited = set(cp['scheduled']), set(cp['visited'])
                    routes, attempted = cp['routes'], cp['attempted']
                hospital.pop('stop_reason',None)
                while queue and (job.get("max_pages") is None or attempted < job["max_pages"]) and not job.get("cancel_requested"):
                    checkpoint(job, hospital_index, queue, scheduled, visited, routes, attempted)
                    save(job)
                    if job.get('pause_requested'):
                        job['status'] = 'paused'
                        return
                    coverage=required_details(job,hospital)
                    hospital['required_details']=coverage
                    contact_pages=sum(1 for page in job['pages'] if page.get('provider_index')==hospital_index and page.get('contact_followup'))
                    reason=early_stop_reason(coverage,queue,contact_pages)
                    if reason:
                        hospital['stop_reason']=reason
                        break
                    priority, depth, url = heapq.heappop(queue)
                    if url in visited:
                        continue
                    visited.add(url)
                    attempted += 1
                    entry = {"hospital": hospital["name"], "url": url, "status": "reading", "detail": "", "provider_index":hospital_index, "contact_followup":coverage["found"]["price"] and priority!=0}
                    entry["crawl_route"] = routes.get(url, [{"url":url,"via":"Source URL"}])
                    job["pages"].append(entry)
                    job["current_url"] = url
                    save(job)
                    if not robot.can_fetch(AGENT, url):
                        entry.update(status="blocked", detail="Disallowed by robots.txt")
                        save(job)
                        continue
                    try:
                        await asyncio.sleep(delay)
                        final, content_type, raw = await fetch(client, url, seed)
                        visited.add(final)
                        if final != url:
                            entry["crawl_route"] = entry["crawl_route"] + [{"url":final,"via":"Redirect destination (intermediate redirects not retained)"}]
                        routes[final] = entry["crawl_route"]
                        entry["content_sha256"] = hashlib.sha256(raw).hexdigest()
                        entry["retrieved_at"] = datetime.now(timezone.utc).isoformat()
                        rendered=False
                        if urlsplit(final).path.endswith('/cms-hpt.txt'):
                            records = []
                            for linked in re.findall(r'https?://[^\s<>"\)]+', raw.decode('utf-8', 'replace')):
                                if same_site(linked, seed):
                                    enqueue(linked, 0, parent=final, via="Transparency-file link")
                                else:
                                    job['warnings'].append('External transparency source requires a separate scan: ' + linked)
                        elif 'csv' in content_type or 'json' in content_type or re.search(r'\.(csv|json)(?:\?|$)', final, re.I):
                            records = await asyncio.to_thread(extract_mrf, raw, final)
                            entry['detail'] = 'Read supported CMS discounted cash fields; excluded other rate types.'
                        elif "pdf" in content_type or raw.startswith(b"%PDF"):
                            def read_pdf():
                                from pdf_ocr import ocr_page
                                reader = PdfReader(io.BytesIO(raw))
                                result, chars, ocr_count = [], 0, 0
                                failures=[]
                                for number, page in enumerate(reader.pages, 1):
                                    if job.get('pause_requested') or job.get('cancel_requested') or job.get('_worker_stop_requested'):break
                                    rows,characters=extract_pdf_page(page,final,number)
                                    # Also inspect image-bearing pages with a text header/footer.
                                    if characters<80 or (not rows and bool(page.images)):
                                        try:
                                            ocr_rows,ocr_chars=ocr_page(raw,number,final)
                                            rows.extend(ocr_rows);characters+=ocr_chars;ocr_count+=1
                                        except Exception as exc:failures.append(f'Page {number}: OCR failed: {exc}')
                                    chars += characters;result.extend(rows)
                                return result, chars, len(reader.pages),ocr_count,failures
                            records, chars, count,ocr_count,failures = await limited(limits['pdf'],asyncio.to_thread,read_pdf)
                            if job.get('pause_requested'):
                                job['status']='paused';return
                            entry['detail']=f'Read {count} PDF pages; OCR checked {ocr_count} pages.'
                            if job.get('cancel_requested'):
                                entry['status']='partial';entry['detail']='PDF processing stopped by user.'
                            if failures or chars<40:
                                entry['status']='partial'
                                entry['detail']+=' '+('; '.join(failures[:5]) or 'No readable text found.')
                        elif "html" in content_type or raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                            from browser_render import needs_render,render_page
                            records=extract_html(raw,final)
                            if needs_render(raw,bool(records),priority):
                                try:
                                    rendered_url,rendered_raw,render_warnings=await limited(limits['browser'],render_page,final,seed,raw)
                                    if rendered_url!=final:
                                        entry['crawl_route'].append({'url':rendered_url,'via':'JavaScript navigation'})
                                    final=rendered_url;raw=rendered_raw;rendered=True
                                    entry['content_sha256']=hashlib.sha256(raw).hexdigest()
                                    entry['detail']='JavaScript-rendered page.'
                                    if render_warnings:
                                        entry['status']='partial';entry['detail']+=' Some resources unavailable: '+'; '.join(render_warnings)
                                    records=extract_html(raw,final)
                                except Exception as exc:
                                    entry['status']='partial';entry['detail']='JavaScript rendering failed; static evidence retained: '+str(exc)
                            soup = BeautifulSoup(raw, "html.parser")
                            contacts, entities = extract_contacts(raw, final)
                            known_contacts={(c['record_id'],c['field'],c['value'],c['source_url']) for c in job['contacts']}
                            baseline=next((r for r in job.get('baseline_records',[]) if r['id']==hospital.get('record_id')),{})
                            for contact in contacts:
                                scope_issue=location_scope_issue(baseline,final)
                                if scope_issue:contact['scope_issues']=[scope_issue];contact['scope']+='; '+scope_issue
                                contact.update(record_id=hospital.get('record_id') or '',hospital=hospital['name'],retrieved_at=entry['retrieved_at'],crawl_route=entry['crawl_route'],content_sha256=entry['content_sha256'])
                                key=(contact['record_id'],contact['field'],contact['value'],contact['source_url'])
                                if key not in known_contacts:
                                    job['contacts'].append(contact);known_contacts.add(key)
                            known_entities={(e['name'],e['address']) for e in job['entities']}
                            for entity in entities:
                                if (entity['name'],entity['address']) not in known_entities:
                                    entity['crawl_route']=entry['crawl_route'];entity['content_sha256']=entry['content_sha256'];entity['retrieved_at']=entry['retrieved_at'];job['entities'].append(entity);known_entities.add((entity['name'],entity['address']))
                            for link in soup.select("a[href]"):
                                linked = urljoin(final, link['href'])
                                enqueue(linked, depth + 1, link.get_text(" ", strip=True), parent=final, via="Internal link: "+link.get_text(" ", strip=True)[:120])
                                if linked.startswith(('http://', 'https://')) and not same_site(linked, seed) and PRIORITY.search(link.get_text(' ', strip=True) + linked):
                                    warning = 'External pricing source requires a separate scan: ' + linked
                                    if warning not in job['warnings']:
                                        job['warnings'].append(warning)
                            if len(soup.get_text(" ", strip=True)) < 100 and not records and not contacts:
                                entry["detail"] += " Little readable text after extraction; content may be blocked or require interaction."
                                entry["status"] = "partial"
                        else:
                            records = []
                            entry.update(status="unsupported", detail="Unsupported content type: " + content_type)
                        verification='not checked'
                        verification_note='No second fetch performed.'
                        repeated=set()
                        def signature(record):
                            return (record.get('service'),record.get('amount'),record.get('currency'),record.get('evidence'),record.get('context'),record.get('page'))
                        if records and job.get('verify_prices',True):
                            try:
                                await asyncio.sleep(delay)
                                check_url,check_type,check_raw=await fetch(client,final,seed)
                                if check_url!=final:
                                    verification='source changed';verification_note='Second fetch redirected to a different URL.'
                                elif 'pdf' in content_type or raw.startswith(b'%PDF'):
                                    verification='repeated source match' if hashlib.sha256(check_raw).hexdigest()==entry['content_sha256'] else 'source changed'
                                    verification_note='PDF file bytes compared across two fetches; this does not verify fee scope.'
                                else:
                                    if 'csv' in content_type or 'json' in content_type or re.search(r'\.(csv|json)(?:\?|$)',final,re.I):
                                        checked=await asyncio.to_thread(extract_mrf,check_raw,final)
                                    else:
                                        if rendered:
                                            rendered_check_url,check_raw,_=await limited(limits['browser'],render_page,check_url,seed,check_raw)
                                            if rendered_check_url!=final:raise ValueError('Rendered source URL changed on recheck')
                                        checked=extract_html(check_raw,final)
                                    repeated={signature(r) for r in checked}
                                    verification='repeated source match'
                                    verification_note='Amount, service and supporting text found on two fetches of this source. Not independent corroboration or a final-bill guarantee.'
                            except Exception as exc:
                                verification='recheck failed';verification_note='Second source fetch failed: '+str(exc)
                        for candidate in records:
                            candidate['verification_status']=verification
                            candidate['verification_note']=verification_note
                            if verification=='repeated source match' and not ('pdf' in content_type or raw.startswith(b'%PDF')) and signature(candidate) not in repeated:
                                candidate['verification_status']='source changed'
                                candidate['verification_note']='This amount/service text was not reproduced on the second fetch.'
                            candidate['crawl_route']=entry['crawl_route']
                            candidate['content_sha256']=entry['content_sha256']
                            candidate['verified_at']=datetime.now(timezone.utc).isoformat()
                        known = {record["id"] for record in job["results"]}
                        for record in records:
                            record = enrich(record)
                            if record is None:
                                continue
                            baseline = next((r for r in job.get('baseline_records', []) if r['id'] == hospital.get('record_id')), {})
                            scope_issue=location_scope_issue(baseline,record['source_url'])
                            if scope_issue:
                                record.setdefault('extraction_issues',[]).append(scope_issue);record['confidence']='needs review'
                            record['record_id'] = hospital.get('record_id') or ''
                            record['baseline_scope'] = baseline.get('baseline', '')
                            record['id'] = record['id'] + '-' + record['record_id']
                            if record["id"] not in known:
                                record.update(hospital=hospital["name"], retrieved_at=datetime.now(timezone.utc).isoformat())
                                job["results"].append(record)
                                known.add(record["id"])
                        entry.update(url=final, matches=sum(1 for r in job["results"] if r["source_url"]==final and r["record_id"]==(hospital.get("record_id") or "")))
                        if entry["status"] == "reading":
                            entry["status"] = "read"
                    except Exception as exc:
                        entry.update(status="error", detail=str(exc))
                    save(job)
                hospital['required_details']=required_details(job,hospital)
                hospital['stop_reason']=hospital.get('stop_reason') or ('Stopped' if job.get('cancel_requested') else 'Page limit reached' if queue else 'Available routes checked')
                hospital["remaining_routes"] = len(queue)
                hospital["attempted_pages"] = attempted
                hospital["discovered_routes"] = len(scheduled)
                if queue and hospital['stop_reason'] in ('Stopped','Page limit reached'):
                    job["warnings"].append(f"{hospital['name']}: {len(queue)} discovered routes remain because the scan stopped or reached its page limit.")
                checkpoint(job, hospital_index + 1)
                save(job)
        job["status"] = "cancelled" if job.get("cancel_requested") else "completed"
    except asyncio.CancelledError:
        job["status"]="paused"
        job["warnings"].append("The server stopped during this scan. Resume to continue from the last saved page checkpoint.")
        raise
    except Exception as exc:
        job.update(status="failed", error=str(exc))
    finally:
        job["current_url"] = ""
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        save(job)
