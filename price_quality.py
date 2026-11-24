"""Conservative price eligibility and semantic identity; original evidence is preserved."""
import re
import json
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

EXAM=re.compile(r'\b(?:MRI|MR|CTA?|CAT|PET|DEXA|DXA|ultrasound|mammogram|mammography|x[ -]?rays?|echocardiogram|arthrogram|magnetic resonance|computed tomography|bone density|biopsy|screening)\b',re.I)


def normalized(value):
    text=unicodedata.normalize('NFKC',str(value or '')).casefold().replace('&',' and ')
    return re.sub(r'[^\w]+',' ',text).strip()


def source_key(url):
    parts=urlsplit(url or '')
    return urlunsplit((parts.scheme.lower(),parts.netloc.lower(),parts.path.rstrip('/'),parts.query,''))


def source_issues(url,evidence):
    path=urlsplit(url or '').path
    issues=[]
    if re.search(r'(?:^|/)(?:patient[-_]?reviews?|testimonials?|patient[-_]?stories|reviews)(?:/|$)',path,re.I):
        issues.append('Patient review/testimonial page is not an official published price list')
    if re.search(r'\bco[- ]?pay\b|\bdeductible\b|\bwould have (?:been|paid|cost)\b',evidence or '',re.I):
        issues.append('Insurance cost or hypothetical comparison, not a published self-pay offer')
    if re.search(r'\b(?:I|my|we)\s+(?:needed|paid|saved|was quoted|had to pay|went|visited)\b',evidence or '',re.I):
        issues.append('Personal anecdote is not an official published self-pay offer')
    return issues


def clean_service(label):
    label=re.sub(r'\s+',' ',label or '').strip(' \"\u201c\u201d:|-')
    # Only remove known presentation wrappers. Never invent an exam/body part.
    label=re.sub(r'^(?:we\s+(?:also\s+)?offer|our price for|self[- ]pay price for)\s+', '',label,flags=re.I)
    label=re.sub(r'\s+(?:for|from|at|is|costs?|price)\s*[:|-]?$', '',label,flags=re.I)
    label=re.sub(r'\s*\|\s*(?:cash|self[- ]pay|discounted cash)(?:\s+(?:price|rate|amount))?(?:\s*\(?USD\)?)?\s*\|?$', '',label,flags=re.I)
    return label.strip(' :|-')


def qualify(record):
    record=dict(record)
    record.setdefault('original_service',record.get('service',''))
    label=clean_service(record.get('service',''))
    issues=list(record.get('extraction_issues',[]))+source_issues(record.get('source_url',''),record.get('evidence',''))
    if len(label)>140 or not EXAM.search(label) and not record.get('cpt'):
        issues.append('Service label is unresolved; inspect the original evidence')
    if re.search(r'\b(?:I|my)\b|would have|insurance co',label,re.I):
        issues.append('Narrative text cannot be used as an exam name')
    if re.search(r'^(?:every|our)\s+.*\b(?:package|option)s?\b.*\bprice\b',label,re.I):
        issues.append('Introductory pricing prose is not an exam name; use the specific published offer')
    record['service']=label or 'Unresolved service'
    record['extraction_issues']=list(dict.fromkeys(issues))
    if issues:record['confidence']='needs review'
    return record


def observation_key(record):
    try:amount=str(Decimal(str(record.get('amount',''))).normalize())
    except InvalidOperation:amount=str(record.get('amount',''))
    return (record.get('record_id',''),record.get('hospital',''),source_key(record.get('source_url','')),
            normalized(clean_service(record.get('service',''))),amount,record.get('currency',''),
            record.get('contrast',''),record.get('cpt',''),record.get('qualifier',''),record.get('price_type',''),normalized(record.get('context','')),record.get('page'))


def proposal_key(p):
    value=p.get('value','')
    if isinstance(value,dict):value=json.dumps(value,sort_keys=True)
    if p.get('field')=='Current supported call number':
        digits=re.sub(r'\D','',value);value=digits[1:] if len(digits)==11 and digits.startswith('1') else digits
    return (p.get('scan_id',''),p.get('record_id',''),p.get('kind'),p.get('field'),normalized(value),source_key(p.get('source_url','')),normalized(p.get('scope','')))
