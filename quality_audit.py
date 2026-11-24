"""Reversible review-queue cleanup; does not modify directory records or applied values."""
import json
from enrichment import now
from price_quality import source_issues, qualify, proposal_key, clean_service


def audit_proposals(store,jobs,dry_run=True):
    findings={(job['id'],r['id']):qualify(r) for job in jobs for r in job.get('results',[])}
    proposals=store.proposals();updates=[];seen={};summary={'invalidated':0,'duplicates':0,'labels_cleaned':0,'applied_warnings':0}
    proposals.sort(key=lambda p:({'applied':0,'approved':1,'pending':2}.get(p['status'],3),p.get('created_at','')))
    for p in proposals:
        if p['status'] in ('invalidated','rejected'):continue
        original=json.loads(json.dumps(p));issues=[]
        is_price=p['kind']=='price_evidence' or 'self-pay (USD)' in p.get('field','')
        if is_price:
            issues=source_issues(p.get('source_url',''),p.get('evidence',''))
            related=[findings[(p.get('scan_id'),rid)] for rid in p.get('source_result_ids',[]) if (p.get('scan_id'),rid) in findings]
            issues+= [issue for r in related for issue in r.get('extraction_issues',[])]
            if issues:
                reason='Price quality check: '+'; '.join(dict.fromkeys(issues))
                if p['status']=='applied':
                    p['quality_warning']=reason;summary['applied_warnings']+=1
                else:
                    p.update(status='invalidated',invalidation_reason=reason,invalidated_at=now());summary['invalidated']+=1
            elif p['status']!='applied' and p['kind']=='price_evidence' and ': USD ' in str(p.get('value','')):
                label,rest=p['value'].split(': USD ',1)
                cleaned=clean_service(label)
                if cleaned!=label:
                    p.setdefault('original_value',p['value']);p['value']=cleaned+': USD '+rest;summary['labels_cleaned']+=1
        if p['status']!='invalidated':
            key=proposal_key(p)
            if key in seen and p['status']!='applied':
                p.update(status='invalidated',invalidation_reason='Duplicate of update '+seen[key]+' in the same scan; original evidence retained.',invalidated_at=now(),duplicate_of=seen[key]);summary['duplicates']+=1
            else:seen[key]=p['id']
        if p!=original:updates.append(p)
    if not dry_run:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for p in updates:db.execute('UPDATE proposals SET body=? WHERE id=?',(json.dumps(p,ensure_ascii=False),p['id']))
    return summary
