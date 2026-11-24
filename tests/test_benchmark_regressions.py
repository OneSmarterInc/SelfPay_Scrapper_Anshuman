from extraction import extract_html
from imaging import enrich

def parsed(html):return [enrich(r) for r in extract_html(html,'https://example.com/prices')]

def test_priced_headings_do_not_duplicate_parent_or_pollute_identity():
    rows=parsed('<header><h1>Affordable Cash Prices</h1></header><div><h2>Pricing</h2><h3>CT without contrast $300</h3><h3>CT with contrast $375</h3><h3>MR without contrast $425</h3><h3>MR with contrast $550</h3></div>')
    assert len(rows)==4
    assert [r['modality'] for r in rows]==['CT','CT','MRI','MRI']
    assert [r['contrast'] for r in rows]==['without contrast','with contrast','without contrast','with contrast']
    assert all(r['price_type']=='self-pay / cash' and not r['extraction_issues'] for r in rows)

def test_nearby_self_pay_caption_retained_without_becoming_service():
    rows=parsed('<div><p>Current self-pay diagnostic option</p><h2>Diagnostic MRI - W/O Contrast: $399</h2></div>')
    assert len(rows)==1 and rows[0]['service']=='Diagnostic MRI - W/O Contrast'
    assert rows[0]['contrast']=='without contrast' and rows[0]['price_type']=='self-pay / cash'

def test_unrelated_previous_exam_heading_cannot_change_modality():
    rows=parsed('<h1>Prices</h1><h2>MRI</h2><p>CT with contrast $300</p>')
    assert rows[0]['modality']=='CT' and rows[0]['contrast']=='with contrast'

def test_plural_xrays_are_not_unresolved():
    rows=parsed('<h1>Self-pay</h1><p>X-rays $45</p>')
    assert not rows[0]['extraction_issues']
