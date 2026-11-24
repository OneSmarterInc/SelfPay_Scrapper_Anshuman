"""US imaging enrichment and CMS discounted-cash readers."""
from price_quality import qualify
import csv
import hashlib
import io
import json
import re
from decimal import Decimal, InvalidOperation

IMAGING = re.compile(r'\b(MRI|MR\b|magnetic resonance|CTA?\b|CAT scan|computed tomography|ultrasound|sonograph|mammogra|x[- ]?ray|radiograph|DEXA|DXA|PET\b|nuclear|fluoroscop|echocardiogram|arthrogram)', re.I)


def enrich(record):
    text = record['service'] + ' ' + record['evidence'] + ' ' + record.get('context', '')
    record.setdefault('cpt', ', '.join(re.findall(r'(?:CPT|HCPCS)\s*[:#]?\s*(\d{5}|[A-Z]\d{4})', text, re.I)))
    if not IMAGING.search(text) and not any(70000 <= int(c) <= 79999 for c in re.findall(r'\b\d{5}\b', record.get('cpt', ''))):
        return None
    if record['currency'] == 'unspecified dollar':
        record['currency'] = 'USD'
        record['reason'] += ' Dollar symbol interpreted in the configured US directory context.'
    lower = text.lower()
    record.setdefault('price_type', 'unspecified — not confirmed self-pay')
    if 'payer' in lower or 'negotiated' in lower or 'insurance rate' in lower:
        record['price_type'] = 'payer / negotiated — not self-pay'
    elif 'gross charge' in lower or 'chargemaster' in lower or 'list price' in lower:
        record['price_type'] = 'gross charge — not self-pay'
    elif re.search(r'self[- ]?pay|cash prices?|cash rates?|discounted cash|uninsured', lower):
        record['price_type'] = 'self-pay / cash'
    if re.search(r'(?:no|not|do not|does not)\s+(?:offer\s+|accept\s+)?(?:self[- ]?pay|cash)|(?:self[- ]?pay|cash)\s+(?:is\s+)?(?:not available|not accepted)',lower):
        record['price_type']='unspecified — self-pay availability negated in source'
    if record['price_type'] != 'self-pay / cash':
        record['confidence'] = 'needs review'
    modalities = [('PET', r'\bPET\b'), ('MRI', r'\bMRI\b|\bMR\b|magnetic resonance'), ('CT', r'\bCTA?\b|computed tomography'), ('Ultrasound', r'ultrasound|sonograph|echocardiogram'), ('Mammography', r'mammogra'), ('X-ray', r'x[- ]?ray|radiograph'), ('DEXA', r'DEXA|DXA')]
    # Exam identity comes from the service, never an unrelated neighbouring heading.
    identity=record['service']
    identity=re.sub(r'\bw/o\s*', 'without ', identity, flags=re.I)
    identity=re.sub(r'\bw/\s*', 'with ', identity, flags=re.I)
    record['modality'] = ', '.join(name for name, pattern in modalities if re.search(pattern, identity, re.I)) or 'Other imaging'
    record.setdefault('cpt', ', '.join(re.findall(r'(?:CPT|HCPCS)\s*[:#]?\s*(\d{5}|[A-Z]\d{4})', text, re.I)))
    record['contrast'] = 'not specified'
    if re.search(r'without\s+(?:and|&)\s+with\s+contrast|with\s+(?:and|&)\s+without\s+contrast', identity, re.I):
        record['contrast'] = 'without and with contrast'
    elif re.search(r'without contrast|non[- ]?contrast', identity, re.I):
        record['contrast'] = 'without contrast'
    elif re.search(r'with contrast', identity, re.I):
        record['contrast'] = 'with contrast'
    return qualify(record)


def cash_record(description, amount, url, evidence, context='', cpt=''):
    try:
        value = Decimal(str(amount).replace('$', '').replace(',', '').strip())
        if not value.is_finite() or value <= 0:
            return None
    except (InvalidOperation, ValueError):
        return None
    record = {'id': hashlib.sha256((url + evidence).encode()).hexdigest()[:20], 'service': description, 'amount': str(value), 'currency': 'USD',
              'qualifier': 'listed', 'evidence': evidence, 'context': context, 'source_url': url, 'page': None,
              'method': 'CMS machine-readable file', 'confidence': 'needs review', 'reason': 'Explicit discounted cash field. Verify facility, setting, code and professional/technical components before updating the directory.',
              'review': 'unreviewed', 'price_type': 'self-pay / cash', 'cpt': cpt}
    return enrich(record)


def extract_mrf(raw, url):
    text = raw.decode('utf-8-sig')
    result = []
    if text.lstrip().startswith('{'):
        document = json.loads(text)
        for item in document.get('standard_charge_information', []):
            description = item.get('description', '')
            codes = ', '.join(str(c.get('code', '')) for c in item.get('code_information', []) if c.get('type', '').upper() in ('CPT', 'HCPCS'))
            for charge in item.get('standard_charges', []):
                evidence = json.dumps({'description': description, 'code_information': item.get('code_information'), 'discounted_cash': charge.get('discounted_cash'), 'setting': charge.get('setting'), 'billing_class': charge.get('billing_class')}, ensure_ascii=False)
                context = json.dumps({'hospital_name': document.get('hospital_name'), 'last_updated_on': document.get('last_updated_on'), 'setting': charge.get('setting'), 'billing_class': charge.get('billing_class')})
                record = cash_record(description, charge.get('discounted_cash'), url, evidence, context, codes)
                if record:
                    result.append(record)
    else:
        rows = csv.reader(io.StringIO(text))
        headers = None
        for index, row in enumerate(rows):
            normalized = [re.sub(r'\s+', '', c).lower() for c in row]
            if 'standard_charge|discounted_cash' in normalized and 'description' in normalized:
                headers = normalized
                break
            if index > 10:
                break
        if headers is None:
            raise ValueError('CSV does not contain supported CMS description and discounted_cash columns.')
        for row in rows:
            fields = dict(zip(headers, row))
            codes = ', '.join(v for k,v in fields.items() if re.fullmatch(r'code\|\d+', k) and fields.get(k + '|type', '').upper() in ('CPT', 'HCPCS'))
            evidence = json.dumps({k:v for k,v in fields.items() if k in ('description', 'standard_charge|discounted_cash', 'setting', 'billing_class') or k.startswith('code|')})
            record = cash_record(fields.get('description', ''), fields.get('standard_charge|discounted_cash'), url, evidence, fields.get('setting', '') + ' ' + fields.get('billing_class', ''), codes)
            if record:
                result.append(record)
    return list({r['id']: r for r in result}.values())
