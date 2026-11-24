"""Transactional working directory, proposals and reversible enrichment audit."""
from price_quality import qualify, observation_key, proposal_key, source_issues
import csv
import hashlib
import io
import json
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from directory import parse_directory
from exam_matching import exam_scope
from facility_matching import FacilityIndex, field_key, location_scope_issue
from contact_values import MULTI_FIELDS, contains, append as append_contact

CONTACT_FIELDS=['Current supported address','Current supported call number','Current usable email']
PRICE_FIELD='New self-pay price evidence'
MISSING=re.compile(r'^(?:n/?a|none|unknown|not (?:available|listed|provided|published|found|verified)|missing|unverified|[-–—])$',re.I)


def now(): return datetime.now(timezone.utc).isoformat()
def missing(value): return not str(value or '').strip() or bool(MISSING.fullmatch(str(value).strip()))
def norm(value): return re.sub(r'[^a-z0-9]','',str(value).lower())
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def summarize(records):
    return {'rows':len(records),'with_urls':sum(bool(r['urls']) for r in records),'categories':dict(Counter(r['category'] for r in records)),
            'missing_fields':{f:sum(missing(r['source'].get(f)) for r in records) for f in CONTACT_FIELDS},
            'with_missing_contacts':sum(any(missing(r['source'].get(f)) for f in CONTACT_FIELDS) for r in records)}


def refresh(record):
    source=record['source']
    extra=[]
    for field in ['Current official evidence','Official pricing / program source','Additional official sources','Lead: Official sources','Email source','Phone source']:
        extra.extend(re.findall(r'https?://[^\s<>"\)]+',source.get(field,'')))
    record['urls']=list(dict.fromkeys(record.get('urls',[])+extra))
    for attr,key in [('name','Center or operator'),('city','City / area'),('state','State'),('category','Record category'),('scope','Record scope'),('baseline','Current supported offer / price scope'),('original_mri','MRI self-pay (USD)'),('original_ct','CT self-pay (USD)')]:
        record[attr]=source.get(key,record.get(attr,''))
    record['missing_fields']=[f for f in CONTACT_FIELDS if missing(source.get(f))]
    record['missing_price_fields']=[f for f in ['MRI self-pay (USD)','CT self-pay (USD)'] if missing(source.get(f))]
    record['new_price_evidence']=source.get(PRICE_FIELD,'')
    return record


class Store:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(exist_ok=True,parents=True)
        self.path=self.folder/'enrichment.sqlite3'
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);')
            for stored in db.execute('SELECT id,body FROM proposals').fetchall():
                proposal=json.loads(stored[1])
                if proposal.get('proposal_version',0)<2 and proposal['status'] not in ('applied','invalidated'):
                    proposal.update(status='invalidated',invalidation_reason='Older draft proposal; regenerate from scan evidence before applying.')
                    db.execute('UPDATE proposals SET body=? WHERE id=?',(json.dumps(proposal),stored[0]))
            initialized=db.execute("SELECT value FROM metadata WHERE key='initialized'").fetchone()
            if not initialized:
                original=self.folder/'directory.json'
                if original.exists():
                    data=json.loads(original.read_text(encoding='utf-8'))
                    for row in data['records']: db.execute('INSERT OR IGNORE INTO records VALUES (?,?)',(row['id'],json.dumps(refresh(row))))
                db.execute("INSERT INTO metadata VALUES ('initialized','1')")
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        db.execute('PRAGMA foreign_keys=ON');return db
    def directory(self):
        with self.connect() as db: records=[refresh(json.loads(row[0])) for row in db.execute('SELECT body FROM records ORDER BY rowid')]
        return {'records':records,'summary':summarize(records)}
    def import_data(self,data):
        # Imports merge new IDs only. Existing edited records cannot be silently reset.
        added=unchanged=conflicts=0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in data['records']:
                existing=db.execute('SELECT body FROM records WHERE id=?',(row['id'],)).fetchone()
                if existing:
                    if json.loads(existing[0])['source']==row['source']: unchanged+=1
                    else: conflicts+=1
                    continue
                db.execute('INSERT INTO records VALUES (?,?)',(row['id'],json.dumps(refresh(row))));added+=1
        return {'added':added,'unchanged':unchanged,'conflicts_preserved':conflicts}
    def proposals(self,status=''):
        with self.connect() as db: result=[json.loads(r[0]) for r in db.execute('SELECT body FROM proposals ORDER BY rowid DESC')]
        return [p for p in result if not status or p['status']==status]
    def add_proposals(self,items):
        with self.connect() as db:
            existing={proposal_key(json.loads(row[0])) for row in db.execute('SELECT body FROM proposals') if json.loads(row[0]).get('status')!='invalidated'}
            for item in items:
                semantic=proposal_key(item)
                if semantic in existing: continue
                existing.add(semantic)
                item.setdefault('id',digest([item.get('scan_id'),item['kind'],item.get('record_id'),item.get('field'),item['value'],item.get('source_url'),item.get('source_result_ids',[])])[:24])
                item.setdefault('status','pending');item.setdefault('created_at',now());item['proposal_version']=2
                db.execute('INSERT OR IGNORE INTO proposals VALUES (?,?)',(item['id'],json.dumps(item)))
    def review(self,ids,status,replace_existing=False):
        if status not in ('approved','rejected','pending'): raise ValueError('Invalid review status')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for key in dict.fromkeys(ids):
                found=db.execute('SELECT body FROM proposals WHERE id=?',(key,)).fetchone()
                if not found: raise ValueError('Proposal not found')
                p=json.loads(found[0])
                if status=='approved' and p.get('kind') in ('price_evidence','fill') and (p.get('kind')=='price_evidence' or 'self-pay (USD)' in p.get('field','')) and source_issues(p.get('source_url',''),p.get('evidence','')):
                    raise ValueError('This evidence is a testimonial or insurance comparison and cannot be approved as a self-pay price.')
                if p['status']=='applied': raise ValueError('Applied proposals cannot be re-reviewed; use Undo in change history.')
                if p['status']=='invalidated': raise ValueError('Source evidence was rejected. Run a new scan or rebuild remaining eligible findings.')
                if p['kind']=='conflict':p['replace_existing']=bool(status=='approved' and replace_existing)
                p.update(status=status,reviewed_at=now())
                db.execute('UPDATE proposals SET body=? WHERE id=?',(json.dumps(p),key))
    def reject_source(self,scan_id,result_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            affected=[]
            for row in db.execute('SELECT body FROM proposals'):
                proposal=json.loads(row[0])
                if proposal.get('scan_id')==scan_id and result_id in proposal.get('source_result_ids',[]): affected.append(proposal)
            if any(p['status']=='applied' for p in affected):
                raise ValueError('Undo applied updates based on this evidence in Change history before rejecting it.')
            for p in affected:
                p.update(status='invalidated',invalidation_reason='Originating scan evidence was rejected',invalidated_at=now())
                db.execute('UPDATE proposals SET body=? WHERE id=?',(json.dumps(p),p['id']))

    def apply(self,ids):
        outcomes=[]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for key in dict.fromkeys(ids):
                found=db.execute('SELECT body FROM proposals WHERE id=?',(key,)).fetchone()
                if not found: raise ValueError('Proposal not found')
                p=json.loads(found[0])
                if p['status']=='applied': outcomes.append({'id':key,'status':'already applied'});continue
                if p['status']!='approved': raise ValueError('Only explicitly approved proposals can be applied.')
                if (p.get('kind')=='price_evidence' or 'self-pay (USD)' in p.get('field','')) and source_issues(p.get('source_url',''),p.get('evidence','')):
                    outcomes.append({'id':key,'status':'blocked','reason':'Testimonial or insurance comparison is not a published self-pay offer.'});continue
                if p['kind']=='new_record':
                    proposed=p['value']; allrows=[json.loads(r[0]) for r in db.execute('SELECT body FROM records')]
                    duplicates=[c['id'] for c in FacilityIndex(allrows).candidates(proposed)]
                    if duplicates:
                        outcomes.append({'id':key,'status':'blocked','reason':'Possible existing location: '+', '.join(dict.fromkeys(duplicates))[:300]});continue
                    rid='NEW-'+key[:12]
                    if db.execute('SELECT 1 FROM records WHERE id=?',(rid,)).fetchone():
                        outcomes.append({'id':key,'status':'blocked','reason':'Record ID already exists'});continue
                    source={'Center or operator':proposed['name'],'City / area':proposed.get('city',''),'State':proposed.get('state',''), 'Stable source record ID':rid,'Record category':'Pending','Record scope':'Discovered location; self-pay eligibility requires verification','Verification status':'New source lead — review required','Current supported address':proposed['address'],'Current official evidence':p['source_url'],'Official pricing / program source':p['source_url'],'Current supported offer / price scope':'Self-pay offer not established','Enrichment checked at':now()}
                    row={'id':rid,'source_row':None,'name':proposed['name'],'source':source,'urls':[p['source_url']],'status':source['Verification status'],'caveats':'','original_mri':'','original_ct':''}
                    row=refresh(row); before=None
                else:
                    rid=p['record_id'];match=db.execute('SELECT body FROM records WHERE id=?',(rid,)).fetchone()
                    if not match: outcomes.append({'id':key,'status':'blocked','reason':'Record no longer exists'});continue
                    row=json.loads(match[0]);before=json.loads(match[0]);field=p['field'];current=row['source'].get(field,'')
                    if p['kind']=='contact_add':
                        if field not in MULTI_FIELDS:
                            outcomes.append({'id':key,'status':'blocked','reason':'Unsupported contact field.'});continue
                        row['source'][field]=append_contact(field,'' if missing(current) else current,p['value'])
                    elif p['kind']=='fill':
                        if current!=p.get('old_value','') or not missing(current):
                            outcomes.append({'id':key,'status':'blocked','reason':'Field changed or already contains a value. Existing data preserved.'});continue
                        row['source'][field]=p['value']
                    elif p['kind']=='conflict' and p.get('replace_existing'):
                        if current!=p.get('old_value',''):
                            outcomes.append({'id':key,'status':'blocked','reason':'Existing value changed after review. Refresh and verify again.'});continue
                        row['source'][field]=p['value']
                    elif p['kind']=='price_evidence':
                        # New independently scoped observations never replace a legacy quote.
                        entry=p['value']+' | '+p['source_url']+' | retrieved '+p.get('retrieved_at',p['created_at'])
                        if entry not in current.splitlines(): row['source'][field]=(current+'\n'+entry).strip()
                    else: outcomes.append({'id':key,'status':'blocked','reason':'Conflicting existing value; use source review rather than automatic replacement.'});continue
                    evidence=json.loads(row['source'].get('Enrichment provenance JSON','[]'))
                    evidence.append({'proposal_id':key,'field':field,'source_url':p['source_url'],'value':p['value'],'scope':p.get('scope',''),'retrieved_at':p.get('retrieved_at',''),'applied_at':now(),'crawl_route':p.get('crawl_route',[]),'verification_status':p.get('verification_status',''),'content_sha256':p.get('content_sha256','')})
                    row['source']['Enrichment provenance JSON']=json.dumps(evidence,ensure_ascii=False)
                    row['source']['Enrichment checked at']=now();row=refresh(row)
                db.execute('INSERT INTO records VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(rid,json.dumps(row)))
                p.update(status='applied',applied_at=now(),applied_record_id=rid)
                db.execute('UPDATE proposals SET body=? WHERE id=?',(json.dumps(p),key))
                audit={'id':uuid.uuid4().hex,'proposal_id':key,'record_id':rid,'field':p.get('field','New location'),'before':before,'after':row,'at':now(),'undone':False}
                db.execute('INSERT INTO audit VALUES (?,?)',(audit['id'],json.dumps(audit)))
                outcomes.append({'id':key,'status':'applied','record_id':rid})
        return outcomes
    def history(self):
        with self.connect() as db: return [json.loads(r[0]) for r in db.execute('SELECT body FROM audit ORDER BY rowid DESC')]
    def undo(self,key):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');found=db.execute('SELECT body FROM audit WHERE id=?',(key,)).fetchone()
            if not found: raise ValueError('Change not found')
            audit=json.loads(found[0])
            if audit['undone']: raise ValueError('This change is already undone')
            current=db.execute('SELECT body FROM records WHERE id=?',(audit['record_id'],)).fetchone()
            if not current or json.loads(current[0])!=audit['after']: raise ValueError('Newer edits exist. Undo the newer changes first.')
            if audit['before'] is None: db.execute('DELETE FROM records WHERE id=?',(audit['record_id'],))
            else: db.execute('UPDATE records SET body=? WHERE id=?',(json.dumps(audit['before']),audit['record_id']))
            audit.update(undone=True,undone_at=now());db.execute('UPDATE audit SET body=? WHERE id=?',(json.dumps(audit),key))
            p=json.loads(db.execute('SELECT body FROM proposals WHERE id=?',(audit['proposal_id'],)).fetchone()[0]);p['status']='pending';db.execute('UPDATE proposals SET body=? WHERE id=?',(json.dumps(p),p['id']))
        return {'undone':key}
    def export(self):
        records=self.directory()['records'];headers=list(dict.fromkeys(k for r in records for k in r['source']))
        output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=headers);writer.writeheader()
        for row in records:
            writer.writerow({k: "'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v for k,v in row['source'].items()})
        return '\ufeff'+output.getvalue()


def build_proposals(job,records):
    job=dict(job)
    baseline_by_id={r['id']:r for r in records}
    unique={}
    for original in job.get('results',[]):
        price=qualify(original)
        issue=location_scope_issue(baseline_by_id.get(price.get('record_id')),price.get('source_url',''))
        if issue:price.setdefault('extraction_issues',[]).append(issue)
        key=observation_key(price)
        if key not in unique:
            price['contributing_ids']=[price['id']] if price.get('id') else []
            unique[key]=price
        elif price.get('id'):
            unique[key]['contributing_ids'].append(price['id'])
    job['results']=list(unique.values())
    indexed={r['id']:r for r in records};items=[]
    unique_contacts={}
    for contact in job.get('contacts',[]):
        if contact.get('review')=='rejected':continue
        value=field_key(contact['field'],contact['value'])
        key=(contact.get('record_id'),contact['field'],value)
        previous=unique_contacts.get(key)
        if previous is None or (contact.get('entity') and not previous.get('entity')): unique_contacts[key]=contact
    for contact in unique_contacts.values():
        row=indexed.get(contact.get('record_id'))
        if not row or contact.get('scope_issues') or location_scope_issue(row,contact.get('source_url','')): continue
        old=row['source'].get(contact['field'],'')
        if contact['field'] in MULTI_FIELDS and contains(contact['field'],old,contact['value']): continue
        if field_key(contact['field'],old)==field_key(contact['field'],contact['value']): continue
        kind='contact_add' if contact['field'] in MULTI_FIELDS else 'fill' if missing(old) else 'conflict'
        items.append(dict(contact,kind=kind,record_name=row['name'],old_value=old,scan_id=job['id']))
    for price in job.get('results',[]):
        row=indexed.get(price.get('record_id'))
        if not row or price.get('price_type')!='self-pay / cash' or price.get('currency')!='USD' or price.get('review')=='rejected': continue
        if price.get('verification_status') in ('source changed','recheck failed','not checked') or price.get('extraction_issues'): continue
        value=f"{price['service']}: {price['currency']} {price['amount']} ({price['qualifier']}; {price.get('contrast','not specified')}; code {price.get('cpt') or 'not stated'})"
        items.append({'kind':'price_evidence','record_id':row['id'],'record_name':row['name'],'field':PRICE_FIELD,'value':value,'old_value':row['source'].get(PRICE_FIELD,''),'evidence':price['evidence'],'scope':row.get('scope','')+'; '+price.get('context',''),'source_url':price['source_url'],'retrieved_at':price.get('retrieved_at',''),'scan_id':job['id'],'crawl_route':price.get('crawl_route',[]),'verification_status':price.get('verification_status','Legacy scan — not rechecked'),'verification_note':price.get('verification_note',''),'content_sha256':price.get('content_sha256',''),'source_result_ids':price.get('contributing_ids',[]), 'exam_scope':exam_scope(price['service']+' '+price.get('qualifier','')+' '+price.get('contrast',''), modality=price.get('modality',''), cpt=price.get('cpt',''), context=price.get('context',''))})
    # Populate genuinely missing price cells as descriptive offers, never bare amounts.
    for row in records:
        related=[p for p in job.get('results',[]) if p.get('record_id')==row['id'] and p.get('price_type')=='self-pay / cash' and p.get('currency')=='USD' and p.get('review')!='rejected' and p.get('verification_status') not in ('source changed','recheck failed','not checked') and not p.get('extraction_issues')]
        for modality,field in [('MRI','MRI self-pay (USD)'),('CT','CT self-pay (USD)')]:
            old=row['source'].get(field,'')
            if not missing(old): continue
            prices=[p for p in related if p.get('modality')==modality]
            if not prices: continue
            descriptions=list(dict.fromkeys(f"{p['service']}: USD {p['amount']} ({p['qualifier']}; {p.get('contrast','not specified')}; CPT/HCPCS {p.get('cpt') or 'not stated'})" for p in prices))
            evidence='\n\n'.join(p['evidence']+' | '+p['source_url'] for p in prices)
            items.append({'kind':'fill','record_id':row['id'],'record_name':row['name'],'field':field,'value':'\n'.join(descriptions),'old_value':old,'source_url':prices[0]['source_url'],'evidence':evidence,'scope':row.get('scope','')+'; Confirm all listed exams and fees before filling the missing price field.','retrieved_at':prices[0].get('retrieved_at',''),'scan_id':job['id'],'crawl_route':prices[0].get('crawl_route',[]),'verification_status':'See each source observation','source_result_ids':list(dict.fromkeys(rid for p in prices for rid in p.get('contributing_ids',[])))})
    for entity in job.get('entities',[]):
        if entity.get('review')=='rejected':continue
        if not entity.get('address') or not entity.get('name'): continue
        items.append({'kind':'new_record','record_id':'','record_name':entity['name'],'field':'New location','value':entity,'old_value':'','evidence':entity['evidence'],'scope':'Published location lead; not yet a verified self-pay provider. Check for alternate names and duplicates.','source_url':entity['url'],'crawl_route':entity.get('crawl_route',[]),'retrieved_at':entity.get('retrieved_at',''),'scan_id':job['id']})
    unique_items={}
    for item in items:
        key=proposal_key(item)
        if key in unique_items:
            previous=unique_items[key]
            previous['source_result_ids']=list(dict.fromkeys(previous.get('source_result_ids',[])+item.get('source_result_ids',[])))
        else:unique_items[key]=item
    return list(unique_items.values())
