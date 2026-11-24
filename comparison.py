"""Display-only comparisons. Never infer equivalent exam scope from amounts alone."""

import re

from enrichment import missing
from exam_matching import compare_exam





def describe(proposal, record=None):

    p = dict(proposal)

    old = p.get('old_value', '')

    label, tone = 'New evidence', 'evidence'

    if p['kind'] == 'fill':

        label, tone = 'Blank field filled' if p['status']=='applied' else 'Fills a blank field', 'filled'

    elif p['kind'] == 'conflict':

        label, tone = 'Different value — review required', 'different'

    elif p['kind'] == 'price_evidence':

        source = (record or {}).get('source', {})

        value = str(p.get('value',''))

        modality = 'MRI' if re.search(r'\bMRI\b', value, re.I) else 'CT' if re.search(r'\bCT\b', value, re.I) else None

        baseline = source.get(modality + ' self-pay (USD)', '') if modality else ''

        baseline = baseline if not missing(baseline) else source.get('Current supported offer / price scope','')

        old = baseline or old

        comparison = compare_exam(old, value, p.get('exam_scope'), modality or '')
        p['exam_comparison'] = comparison
        if comparison['state'] == 'different_scope':
            label = 'Different exam or fee scope — separate offer'
        elif comparison['state'] == 'unknown':
            label = 'Exam match unresolved — review scope'
        elif comparison['amount_changed']:
            label, tone = 'Comparable offer — published amount differs', 'different'
        else:
            label = 'Comparable offer — same published amount'

    p.update(comparison_label=label, comparison_tone=tone, comparison_old=old)

    return p





def audit_values(item):

    if item.get('before') is None:
        record=item.get('after') or {}
        return {'old_value':'No record', 'new_value':record.get('name','')+' - '+record.get('source',{}).get('Current supported address',''), 'record_name':record.get('name',''), 'comparison_tone':'filled', 'change_label':'New location added'}
    field=item['field']

    before=(item.get('before') or {}).get('source',{}).get(field,'')

    after=(item.get('after') or {}).get('source',{}).get(field,'')

    return {'old_value':before, 'new_value':after,

            'record_name':(item.get('after') or {}).get('name',''),

            'comparison_tone':'filled' if missing(before) else 'different'}





def signals(records, proposals, history):

    indexed={r['id']:r for r in records}

    out={}

    for audit in history:

        if audit['undone']: continue

        values=audit_values(audit)

        out.setdefault(audit['record_id'],[]).append(dict(values,field=audit['field'],label=values.get('change_label') or ('Blank field filled' if values['comparison_tone']=='filled' else 'Applied update'),state='applied'))

    for p in proposals:

        if p['status'] not in ('pending','approved'): continue

        p=describe(p,indexed.get(p.get('record_id')))

        if p['comparison_tone'] in ('filled','different'):

            out.setdefault(p.get('record_id'),[]).append({'field':p['field'],'label':p['comparison_label'],'comparison_tone':p['comparison_tone'],'state':p['status'],'old_value':p['comparison_old'],'new_value':p['value']})

    return out

