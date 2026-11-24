"""Read-only center-level presentation; observations never mutate directory records."""
import json
from urllib.parse import urlsplit
from enrichment import missing
import re
from facility_matching import FacilityIndex,name_key,address_parts,field_key
from contact_values import MULTI_FIELDS, contains
FIELDS={'Current supported address':'Address','Current supported call number':'Phone','Current usable email':'Email','MRI self-pay (USD)':'MRI price','CT self-pay (USD)':'CT price','Current supported offer / price scope':'Price scope','New self-pay price evidence':'Price evidence'}

def observation_id(item):
    import hashlib
    stable={k:v for k,v in item.items() if k not in ('review','reviewed_at')}
    return hashlib.sha256(json.dumps(stable,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]

def center_key(record_id,name='',url='',address=''):
    if record_id:return 'record:'+record_id
    if address:return 'location:'+json.dumps([name.casefold().strip(),address.casefold().strip()])
    return 'website:'+json.dumps([name.casefold().strip(),(urlsplit(url).hostname or '').lower()])

def centers(records,proposals,jobs,scan_id=''):
    indexed={r['id']:r for r in records};groups={}
    facilities=FacilityIndex(records)
    def group(rid,name,url,address=''):
        if not rid and address:
            exact=[c for c in facilities.candidates({'name':name,'address':address,'url':url}) if name_key(c['name'])==name_key(name) and address_parts(c['address'])==address_parts(address)]
            if len(exact)==1:rid=exact[0]['id']
        key=center_key(rid,name,url,address)
        if key not in groups:
            r=indexed.get(rid,{})
            groups[key]={'key':key,'record_id':rid or '', 'name':r.get('name') or name or urlsplit(url).hostname or 'Unassigned source','city':r.get('city',''),'state':r.get('state',''),'current':{k:v for k,v in r.get('source',{}).items() if k!='Enrichment provenance JSON'},'observations':[],'proposal_ids':[],'latest_extraction_version':0}
        return groups[key]
    covered_results={(p.get('scan_id'),rid) for p in proposals for rid in p.get('source_result_ids',[])}
    seen=set()
    def add(g,field,value,source,scan,status='observed',proposal=None,old=None,issues=None,route=None,target=None):
        if value in ('',None):return
        value=str(value)
        comparable=lambda v: field_key(field,v)
        if not proposal:
            existing=next((o for o in g['observations'] if o['field']==field and o['scan_id']==scan and (o.get('proposal_id') or o['status']==status) and comparable(o['value'])==comparable(value)),None)
            if existing:
                if target and not existing.get('proposal_id'):
                    existing.setdefault('review_targets',[existing['review_target']] if existing.get('review_target') else []).append(target)
                if source and source not in [s['source_url'] for s in existing['sources']]:existing['sources'].append({'source_url':source,'crawl_route':route or []})
                return
        identity=(g['key'],field,value,source,scan)
        if identity in seen and not proposal:return
        seen.add(identity)
        current=g['current'].get(field,'')
        previous=current if old is None else old
        label='Unchanged' if comparable(value)==comparable(str(previous)) else 'Blank → New' if missing(previous) else 'Old → Different'
        if field in MULTI_FIELDS:
            label='Unchanged' if comparable(value)==comparable(str(previous)) or contains(field,previous,value) else 'Blank → New' if missing(previous) else 'Additional contact'
        if field=='New self-pay price evidence':label='New price evidence — compare scope'
        if issues:label='Needs source review'
        g['observations'].append({'field':field,'value':value,'old_value':previous,'label':label,'status':status,'source_url':source,'scan_id':scan,'proposal_id':proposal,'review_target':target,'issues':issues or [],'crawl_route':route or [],'sources':[{'source_url':source,'crawl_route':route or []}]})
    for p in proposals:
        if scan_id and p.get('scan_id')!=scan_id:continue
        if p['kind']=='new_record':
            entity=p['value'];g=group('',entity['name'],p.get('source_url',''),entity.get('address',''))
            for field,value in [('Current supported address',entity.get('address')),('City / area',entity.get('city')),('State',entity.get('state'))]:add(g,field,value,p.get('source_url',''),p.get('scan_id',''),p['status'],p['id'],old=g['current'].get(field,''),route=p.get('crawl_route'))
        else:
            g=group(p.get('record_id'),p.get('record_name',''),p.get('source_url',''))
            add(g,p['field'],p['value'],p.get('source_url',''),p.get('scan_id',''),p['status'],p['id'],old=p.get('comparison_old',p.get('old_value','')),route=p.get('crawl_route'))
        g['proposal_ids'].append(p['id'])
    for job in jobs:
        if scan_id and job['id']!=scan_id:continue
        hospitals=job.get('hospitals',[])
        for hospital in hospitals:
            g=group(hospital.get('record_id'),hospital.get('name',''),hospital.get('url',''))
            g['latest_extraction_version']=max(g['latest_extraction_version'],job.get('extraction_version',0))
        def owner(item):
            rid=item.get('record_id') or ''
            name=item.get('hospital') or item.get('entity') or ''
            if not name and len(hospitals)==1:name=hospitals[0]['name']
            return group(rid,name,item.get('source_url',''))
        for item in job.get('contacts',[]):
            g=owner(item);add(g,item['field'],item['value'],item.get('source_url',''),job['id'],item.get('review','unreviewed'),route=item.get('crawl_route'),target={'scan_id':job['id'],'collection':'contacts','observation_id':observation_id(item)})
        for item in job.get('results',[]):
            g=owner(item)
            # Avoid showing a proposal and its raw observation twice.
            covered=(job['id'],item.get('id')) in covered_results
            if covered:continue
            value=f"{item['service']}: {item.get('currency','')} {item['amount']} ({item.get('contrast','not specified')}; {item.get('qualifier','listed')})"
            issues=list(item.get('extraction_issues',[]))
            if item.get('price_type')!='self-pay / cash':issues.append('Not confirmed self-pay')
            add(g,'New self-pay price evidence',value,item.get('source_url',''),job['id'],item.get('review','unreviewed'),issues=issues,route=item.get('crawl_route'),target={'scan_id':job['id'],'collection':'results','observation_id':observation_id(item)})
        for item in job.get('entities',[]):
            g=group('',item.get('name',''),item.get('url',''),item.get('address',''))
            for field,value in [('Current supported address',item.get('address')),('City / area',item.get('city')),('State',item.get('state'))]:
                add(g,field,value,item.get('url',''),job['id'],item.get('review','unreviewed'),route=item.get('crawl_route'),target={'scan_id':job['id'],'collection':'entities','observation_id':observation_id(item)})
    for g in groups.values():
        merged={}
        for o in g['observations']:
            field=o['field'];value=field_key(field,o['value'])
            p=next((p for p in proposals if p['id']==o.get('proposal_id')),None)
            scope=json.dumps(p.get('exam_scope',{}),sort_keys=True) if p else ''
            key=(field,value,o['status'],field_key(field,o['old_value']),tuple(o['issues']),p.get('kind') if p else '',scope)
            if key not in merged:
                merged[key]=dict(o,proposal_ids=[],review_targets=[],scan_ids=[],sources=[])
            dest=merged[key]
            for name,items in [('proposal_ids',[o['proposal_id']] if o.get('proposal_id') else []),('review_targets',o.get('review_targets') or ([o['review_target']] if o.get('review_target') else [])),('scan_ids',[o['scan_id']]),('sources',[dict(source,scan_id=o['scan_id']) for source in o['sources']])]:
                for item in items:
                    if item not in dest[name]:dest[name].append(item)
        observations=list(merged.values())
        for o in observations:
            if o.get('proposal_ids') or o.get('issues'):continue
            matches=[p for p in observations if p.get('proposal_ids') and not p.get('issues') and p['status'] in ('pending','approved','applied') and p['field']==o['field'] and field_key(o['field'],p['value'])==field_key(o['field'],o['value'])]
            if matches:
                dest=matches[0]
                for source in o['sources']:
                    if source not in dest['sources']:dest['sources'].append(source)
                for scan in o['scan_ids']:
                    if scan not in dest['scan_ids']:dest['scan_ids'].append(scan)
                o['_duplicate']=True
        g['observations']=[o for o in observations if not o.get('_duplicate')]
    return list(groups.values())
