from extraction import extract_html,extract_text
from imaging import enrich
from price_quality import clean_service, observation_key
from enrichment import Store,build_proposals
import pytest

def test_testimonial_and_copay_are_not_prices():
    assert extract_html('<h1>Self-pay</h1><p>I needed a CT scan. Insurance co-pay would have been $798.</p>','https://example.com/patient-reviews/')==[]
    assert extract_text('CT insurance copay $798','https://example.com/pricing',context='Self-pay')==[]

def test_testimonial_widget_excluded_but_real_offer_retained():
    html='<h1>Self-pay pricing</h1><div class="testimonials"><p>I paid $798 for CT</p></div><p>CT without contrast $300</p>'
    rows=extract_html(html,'https://example.com/pricing')
    assert len(rows)==1 and rows[0]['amount']=='300'

def test_service_cleanup_preserves_scope_and_evidence():
    html='<h1>Self-pay prices</h1><p>We also offer Whole Body Screening MRI for: $1000</p>'
    row=enrich(extract_html(html,'https://example.com/pricing')[0])
    assert row['service']=='Whole Body Screening MRI'
    assert 'We also offer' in row['evidence']

def test_equivalent_blocks_dedup_but_contrast_remains_separate():
    rows=extract_html('<h1>Self-pay</h1><p>MRI without contrast: $500</p><p>MRI without contrast - $500</p><p>MRI with contrast: $500</p>','https://example.com/pricing')
    assert len(rows)==2

def test_rebuild_older_scan_excludes_testimonial():
    row={'id':'r','service':'I needed CT insurance co-pay would have been','evidence':'I needed CT insurance co-pay would have been $798','amount':'798','currency':'USD','source_url':'https://example.com/patient-reviews/','price_type':'self-pay / cash','modality':'CT','record_id':'D-1','review':'unreviewed','verification_status':'repeated source match','qualifier':'listed'}
    baseline={'id':'D-1','name':'Test','source':{},'scope':''}
    assert build_proposals({'id':'scan','results':[row]},[baseline])==[]

def test_direct_bulk_api_cannot_approve_unsafe_stored_evidence(tmp_path):
    store=Store(tmp_path)
    store.add_proposals([{'id':'bad','kind':'price_evidence','field':'New self-pay price evidence','value':'CT USD798','evidence':'Insurance copay $798','source_url':'https://example.com/reviews/'}])
    with pytest.raises(ValueError,match='cannot be approved'):store.review(['bad'],'approved')

def test_semantic_keys_preserve_facility_and_scope():
    a={'record_id':'a','service':'MRI brain','amount':'500.00','currency':'USD','source_url':'https://example.com/prices','context':'Hospital A, technical only'}
    assert observation_key(a)==observation_key(dict(a,amount='500'))
    assert observation_key(a)!=observation_key(dict(a,record_id='b'))
    assert observation_key(a)!=observation_key(dict(a,context='Hospital A, all fees'))


def test_nested_price_spans_use_own_list_item_exam():
    html='<h1>CT Scan Basics</h1><h2>Self-pay prices</h2><ul><li>CT Lung Screening: <span><span>$250</span></span></li><li>CT Head/Brain without contrast: <span>$400</span></li></ul>'
    rows=[enrich(r) for r in extract_html(html,'https://example.com/prices')]
    assert [(r['service'],r['amount']) for r in rows]==[('CT Lung Screening','250'),('CT Head/Brain without contrast','400')]
    assert all(not r['extraction_issues'] for r in rows)


def test_price_only_under_article_heading_needs_review():
    rows=extract_html('<h1>Self-pay CT scan cost guide</h1><span>$250</span><span>$500</span>','https://example.com/prices')
    assert rows and all(r['extraction_issues'] for r in rows)
