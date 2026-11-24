import csv
import hashlib
import json
import io
import re
from collections import Counter


def parse_directory(text):
    csv.field_size_limit(5 * 1024 * 1024)
    try:
        rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff')),strict=True))
    except csv.Error as exc:
        raise ValueError('Invalid CSV: '+str(exc)) from exc
    header_index = next((i for i, row in enumerate(rows[:25]) if 'Center or operator' in row and 'Stable source record ID' in row), None)
    if header_index is None:
        raise ValueError('Expected directory columns: Center or operator and Stable source record ID. The original export with title rows is supported.')
    headers = [header.strip() for header in rows[header_index]]
    if len(set(headers))!=len(headers) or any(not h for h in headers):
        raise ValueError('CSV headers must be unique and non-empty.')
    records = []
    for line, values in enumerate(rows[header_index + 1:], header_index + 2):
        if not any(values):
            continue
        if len(values)>len(headers):
            raise ValueError(f'Row {line} has more columns than the header; check quoting and separators.')
        source = dict(zip(headers, values+['']*(len(headers)-len(values))))
        record_id=source.get('Stable source record ID','').strip()
        if not record_id:
            record_id='IMPORTED-'+hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()[:16]
        source['Stable source record ID']=record_id
        urls = []
        for key in ['Current official evidence', 'Official pricing / program source', 'Additional official sources', 'Lead: Official sources', 'Email source', 'Phone source', 'Additional official sources']:
            urls.extend(re.findall(r'https?://[^\s<>"\)]+', source.get(key, '')))
        records.append({'id': record_id, 'source_row': line,
                        'name': source.get('Center or operator') or source.get('Lead: Center') or 'Unnamed source',
                        'state': source.get('State') or source.get('Lead: State', ''),
                        'city': source.get('City / area') or source.get('Lead: City', ''),
                        'category': source.get('Record category', ''), 'status': source.get('Verification status', ''),
                        'scope': source.get('Record scope', ''),
                        'baseline': source.get('Current supported offer / price scope', ''),
                        'original_mri': source.get('MRI self-pay (USD)', ''), 'original_ct': source.get('CT self-pay (USD)', ''),
                        'caveats': source.get('Current fee / eligibility limits') or source.get('Inclusions and caveats', ''),
                        'urls': list(dict.fromkeys(urls)), 'source': source})
    counts = Counter(r['id'] for r in records)
    duplicates = [key for key, count in counts.items() if count > 1]
    if duplicates:
        raise ValueError('Duplicate stable IDs require resolution before import: ' + ', '.join(duplicates[:10]))
    return {'records': records, 'summary': {'rows': len(records), 'with_urls': sum(bool(r['urls']) for r in records),
            'categories': dict(Counter(r['category'] for r in records)), 'statuses': dict(Counter(r['status'] for r in records)),
            'columns': len(headers), 'header_row': header_index + 1}}
