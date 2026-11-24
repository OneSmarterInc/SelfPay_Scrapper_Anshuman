from snapshot_io import replace_snapshot
import asyncio
import csv
import io
import json
import secrets
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from crawler import crawl, canonical
from directory import parse_directory
from scan_excel import template, validate_files
from facility_matching import FacilityIndex
from comparison import describe, signals, audit_values
from excel_export import directory_workbook, workbook, scan_workbook, MIME
from enrichment import Store, build_proposals

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
JOBS = {}
TASKS = set()
TOKEN = secrets.token_urlsafe(32)
SAVE_LOCK = threading.RLock()

def store():
    return Store(DATA)


def save(job):
    with SAVE_LOCK:
        dest = DATA / (job['id'] + '.json')
        temporary = dest.with_suffix('.tmp')
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        replace_snapshot(temporary,dest)


async def run_scan(job):
    await crawl(job,save)
    if job['status'] == 'paused':
        return
    try:
        repository=store()
        proposals=build_proposals(job,repository.directory()['records'])
        repository.add_proposals(proposals)
        job['enrichment_status']='ready'
        job['proposal_count']=len(proposals)
    except Exception as exc:
        job['enrichment_status']='failed'
        job['warnings'].append('Enrichment proposals could not be saved: '+str(exc))
    save(job)



@asynccontextmanager
async def lifespan(app):
    for file in DATA.glob('*.json'):
        if file.name == 'directory.json':
            continue
        try:
            job = json.loads(file.read_text(encoding='utf-8'))
            if job['status'] in ('queued', 'running'):
                job['status'] = 'paused' if job.get('checkpoint') else 'interrupted'
                job['warnings'].append('The app stopped. Resume from the saved checkpoint.' if job.get('checkpoint') else 'This older scan has no checkpoint. Start a new scan to retry.')
                save(job)
            JOBS[job['id']] = job
        except (ValueError, KeyError):
            continue
    yield
    pending=list(TASKS)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(title='Clearprice Â· Self-pay imaging research', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def protect(request: Request, call_next):
    if request.method in ('POST', 'PATCH', 'DELETE') and request.headers.get('x-app-token') != TOKEN:
        return Response('Refresh the app before making changes.', status_code=403)
    response = await call_next(request)
    if request.url.path.startswith(('/api/','/static/')) or request.url.path=='/':
        response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    return response


@app.get('/api/session')
def session():
    return {'token': TOKEN, 'pause_resume': True, 'local_only': True, 'adjustable_concurrency': True}


def directory():
    return store().directory()


@app.get('/api/directory')
def get_directory():
    repository=store()
    data = repository.directory()
    marks=signals(data['records'],repository.proposals(),repository.history())
    for row in data['records']: row['updates']=marks.get(row['id'],[])
    # Preserve full source fields in storage.
    return {'summary': data['summary'], 'records': [{k:v for k,v in row.items() if k != 'source'} for row in data['records']]}


@app.get('/api/directory/export.csv')
def export_directory():
    return Response(store().export(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="enriched-self-pay-directory.csv"'})


@app.get('/api/directory/export.xlsx')
def export_excel():
    return Response(directory_workbook(store()),media_type=MIME,headers={'Content-Disposition':'attachment; filename="self-pay-directory.xlsx"'})


@app.get('/api/directory/{record_id}')
def get_record(record_id: str):
    record = next((r for r in directory()['records'] if r['id'] == record_id), None)
    if not record:
        raise HTTPException(404, 'Record not found')
    return record


@app.post('/api/directory')
async def import_directory(request: Request):
    if any(j['status'] in ('running', 'queued', 'paused') for j in JOBS.values()):
        raise HTTPException(409, 'Finish or stop paused scans and wait for the active scan before replacing the directory.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 30 * 1024 * 1024:
            raise HTTPException(413, 'CSV import limit is 30 MB.')
    try:
        data = parse_directory(body.decode('utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, str(exc))
    return store().import_data(data)


@app.get('/api/scans/template.xlsx')
def scan_template():
    return Response(template(), media_type=MIME, headers={'Content-Disposition': 'attachment; filename="scan-template.xlsx"'})


class ScanWorkbook(BaseModel):
    name: str = Field(max_length=255)
    content: str = Field(max_length=2800000)


class ScanInput(BaseModel):
    concurrency: int = Field(4, ge=1, strict=True)
    files: list[ScanWorkbook] = Field(default_factory=list, max_length=10)
    urls: list[str] = Field(default_factory=list, max_length=20)
    record_ids: list[str] = Field(default_factory=list, max_length=20000)
    max_pages: int | None = Field(None, ge=1)
    max_depth: int | None = Field(None, ge=0)


@app.post('/api/scans')
async def start_scan(payload: ScanInput):
    if any(j['status'] in ('running', 'queued') for j in JOBS.values()):
        raise HTTPException(409, 'A scan is already active. Stop it or wait for it to finish.')
    hospitals = []
    selected = []
    try:
        for url in dict.fromkeys(payload.urls):
            url = canonical(url)
            hospitals.append({'name': url.split('/')[2], 'url': url, 'record_id': None})
        indexed = {r['id']: r for r in directory()['records']}
        uploaded = validate_files(payload.files, indexed)
        for key in dict.fromkeys(payload.record_ids):
            if key not in indexed:
                raise ValueError(f'Directory record not found: {key}')
            row = indexed[key]
            if not row['urls']:
                raise ValueError(f"{row['name']} has no source URL. Add a verified website first.")
            selected.append(row)
            hospitals.append({'name': row['name'], 'url': canonical(row['urls'][0]), 'seed_urls': row['urls'], 'record_id': key})
        for row in uploaded:
            if row['record_id']:
                selected.append(indexed[row['record_id']])
            hospitals.append(row)
        urls_seen, ids_seen = set(), set()
        for hospital in hospitals:
            rid = hospital.get('record_id')
            if (hospital['url'] in urls_seen and (payload.urls or payload.files)) or (rid and rid in ids_seen):
                raise ValueError('Duplicate provider across Excel files, selected records or typed URLs. Include each provider once.')
            urls_seen.add(hospital['url']); ids_seen.add(rid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not hospitals or len(hospitals) > (20 if payload.urls or payload.files else 20000):
        raise HTTPException(400, 'Select directory records, or use up to 20 providers when including Excel files or typed URLs.')
    job = {'id': uuid.uuid4().hex, 'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'queued',
           'hospitals': hospitals, 'baseline_records': selected, 'concurrency': payload.concurrency, 'max_pages': payload.max_pages, 'max_depth': payload.max_depth,
           'pages': [], 'results': [], 'contacts': [], 'entities': [], 'warnings': [], 'current_url': '', 'cancel_requested': False}
    JOBS[job['id']] = job
    save(job)
    task = asyncio.create_task(run_scan(job))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return {'id': job['id']}


@app.get('/api/scans')
def scans():
    return [{k: j[k] for k in ('id', 'created_at', 'status', 'hospitals')} | {'prices': len(j['results']), 'pages': len(j['pages'])} for j in sorted(JOBS.values(), key=lambda j: j['created_at'], reverse=True)]


def get_job(key):
    if key not in JOBS:
        raise HTTPException(404, 'Scan not found')
    return JOBS[key]


@app.get('/api/scans/{key}')
def scan(key: str):
    return {k:v for k,v in get_job(key).items() if k != "checkpoint"}


@app.post('/api/scans/{key}/cancel')
async def cancel(key: str):
    job = get_job(key)
    if job['status'] in ('running', 'queued'):
        job['cancel_requested'] = True
        save(job)
    if job['status'] == 'paused':
        job.update(status='cancelled', cancel_requested=True)
        save(job)
    return {'status': job['status'], 'cancel_requested': job['cancel_requested']}


@app.post('/api/scans/{key}/pause')
async def pause(key: str):
    job = get_job(key)
    if job['status'] not in ('running', 'queued', 'paused'):
        raise HTTPException(409, 'Only an active scan can be paused.')
    job['pause_requested'] = True
    save(job)
    return {'status': job['status'], 'pause_requested': True}


class ConcurrencyInput(BaseModel):
    concurrency: int = Field(ge=1, strict=True)


@app.post('/api/scans/{key}/concurrency')
async def change_concurrency(key: str, payload: ConcurrencyInput):
    job = get_job(key)
    if job['status'] not in ('queued','running','paused'):
        raise HTTPException(409, 'Only active or paused scans can change concurrency.')
    if job['status']=='running' and job.get('checkpoint',{}).get('version')!=2:
        raise HTTPException(409, 'Pause this older scan before changing concurrency.')
    job['concurrency']=payload.concurrency
    save(job)
    return {'concurrency':job['concurrency']}


@app.post('/api/scans/{key}/resume')
async def resume(key: str):
    job = get_job(key)
    if job['status'] != 'paused' or not job.get('checkpoint'):
        raise HTTPException(409, 'This scan has no paused checkpoint to resume.')
    if any(j['status'] in ('running', 'queued') for j in JOBS.values()):
        raise HTTPException(409, 'Pause or finish the active scan first.')
    job.update(status='queued', pause_requested=False, cancel_requested=False)
    save(job)
    task = asyncio.create_task(run_scan(job))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return {'id': key, 'status': 'queued'}


class Review(BaseModel):
    review: str


@app.patch('/api/scans/{key}/results/{record_id}')
async def review(key: str, record_id: str, payload: Review):
    if payload.review not in ('unreviewed', 'accepted', 'rejected'):
        raise HTTPException(400, 'Invalid review status')
    job = get_job(key)
    record = next((r for r in job['results'] if r['id'] == record_id), None)
    if not record:
        raise HTTPException(404, 'Price not found')
    if payload.review=='rejected':
        try: store().reject_source(key,record_id)
        except ValueError as exc: raise HTTPException(409,str(exc))
    record['review'] = payload.review
    record['reviewed_at'] = datetime.now(timezone.utc).isoformat()
    from parallel_crawler import remember_review
    remember_review(job,'results',record)
    save(job)
    return record


class ObservationReview(BaseModel):
    collection: str
    observation_id: str
    review: str


@app.post('/api/scans/{key}/observations/review')
async def review_observation(key: str, payload: ObservationReview):
    from center_view import observation_id
    if payload.collection not in ('contacts','results','entities') or payload.review not in ('accepted','rejected','unreviewed'):
        raise HTTPException(400,'Invalid observation review')
    job=get_job(key)
    found=next((r for r in job.get(payload.collection,[]) if observation_id(r)==payload.observation_id),None)
    if found is None:raise HTTPException(404,'Finding not found; refresh this view.')
    if payload.review=='rejected':
        repository=store()
        affected=[p for p in repository.proposals() if p.get('scan_id')==key and (found.get('id') in p.get('source_result_ids',[]) or payload.collection=='contacts' and p.get('field')==found.get('field') and p.get('value')==found.get('value') and p.get('record_id')==found.get('record_id') or payload.collection=='entities' and p.get('kind')=='new_record' and all(p.get('value',{}).get(k)==found.get(k) for k in ('name','address','url')))]
        if any(p['status']=='applied' for p in affected):raise HTTPException(409,'Undo the applied update in Change history before rejecting its evidence.')
        if affected:repository.review([p['id'] for p in affected],'rejected')
    found['review']=payload.review
    found['reviewed_at']=datetime.now(timezone.utc).isoformat()
    from parallel_crawler import remember_review
    remember_review(job,payload.collection,found)
    save(job)
    return {'review':payload.review,'directory_changed':False}


@app.get('/api/scans/{key}/export.csv')
def export(key: str, accepted_only: bool = False):
    job = get_job(key)
    columns = ['record_id', 'hospital', 'service', 'amount', 'currency', 'price_type', 'modality', 'cpt', 'contrast', 'qualifier', 'review', 'confidence', 'baseline_scope', 'source_url', 'crawl_route', 'verification_status', 'verification_note', 'content_sha256', 'page', 'evidence', 'context', 'retrieved_at', 'method', 'reason']
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for record in job['results']:
        if accepted_only and record['review'] != 'accepted':
            continue
        # Prevent source strings becoming executable spreadsheet formulas.
        writer.writerow({k: "'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v for k, v in record.items()})
    return Response('\ufeff' + output.getvalue(), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="self-pay-evidence-{key[:8]}.csv"'})


@app.get('/api/scans/{key}/export.xlsx')
def export_scan_excel(key: str, accepted_only: bool=False):
    job=get_job(key)
    return Response(scan_workbook(job,accepted_only),media_type=MIME,headers={'Content-Disposition':f'attachment; filename="price-evidence-{key[:8]}.xlsx"'})


class ProposalReview(BaseModel):
    ids: list[str] = Field(min_length=1,max_length=200)
    status: str = 'approved'
    replace_existing: bool = False


@app.get('/api/enrichment')
def enrichment(status: str = ''):
    repository=store()
    indexed={r['id']:r for r in repository.directory()['records']}
    all_proposals=[describe(p,indexed.get(p.get('record_id'))) for p in repository.proposals()]
    facilities=FacilityIndex(indexed.values())
    for proposal in all_proposals:
        if proposal['kind']=='new_record':
            proposal['duplicate_candidates']=facilities.candidates(proposal['value'], exclude_id=proposal.get('applied_record_id'))
    proposals=[p for p in all_proposals if not status or p['status']==status]
    counts={s:sum(p['status']==s for p in all_proposals) for s in ['pending','approved','applied','rejected','invalidated']}
    return {'proposals':proposals,'counts':counts}


@app.post('/api/enrichment/review')
def review_proposals(payload: ProposalReview):
    try: store().review(payload.ids,payload.status,replace_existing=payload.replace_existing)
    except ValueError as exc: raise HTTPException(409,str(exc))
    return {'updated':len(payload.ids)}


@app.post('/api/enrichment/apply')
def apply_proposals(payload: ProposalReview):
    repository=store()
    for proposal in repository.proposals():
        if proposal['id'] not in payload.ids: continue
        origin=JOBS.get(proposal.get('scan_id'),{})
        for result in origin.get('results',[]):
            if result.get('review')=='rejected' and result['id'] in proposal.get('source_result_ids',[]):
                try: repository.reject_source(origin['id'],result['id'])
                except ValueError as exc: raise HTTPException(409,str(exc))
    try: result=repository.apply(payload.ids)
    except ValueError as exc: raise HTTPException(409,str(exc))
    return {'outcomes':result}


@app.post('/api/scans/{key}/enrichment')
def rebuild_proposals(key: str):
    job=get_job(key)
    if job['status'] in ('running','queued'): raise HTTPException(409,'Wait for the scan to finish first.')
    if job.get('contacts') and job.get('extraction_version',0)<2: raise HTTPException(409,'This scan used an earlier contact extractor. Run a new scan before generating contact updates.')
    repository=store();items=build_proposals(job,repository.directory()['records']);repository.add_proposals(items)
    return {'proposals':len(items)}


@app.get('/api/centers')
def center_findings(scan_id: str = '', record_id: str = ''):
    from center_view import centers
    repository=store()
    records=repository.directory()['records']
    indexed={r['id']:r for r in records}
    proposals=[describe(p,indexed.get(p.get('record_id'))) for p in repository.proposals()]
    result=centers(records,proposals,list(JOBS.values()),scan_id)
    return {'centers':[g for g in result if not record_id or g['record_id']==record_id]}


@app.get('/api/changes')
def changes():
    return [{k:v for k,v in item.items() if k not in ('before','after')} | audit_values(item) for item in store().history()]


@app.post('/api/changes/{key}/undo')
def undo_change(key: str):
    try: return store().undo(key)
    except ValueError as exc: raise HTTPException(409,str(exc))



@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
