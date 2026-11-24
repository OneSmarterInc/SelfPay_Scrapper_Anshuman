import pytest
from extraction import extract_html, extract_text
from enrichment import build_proposals, Store
from imaging import enrich


@pytest.mark.parametrize('text',[
    'A single MRI at regional hospitals runs $1,000–$3,000 and covers just one region.',
    'Other hospitals charge $2000 for an MRI.',
    'The national average MRI cost is $1500.',
    'MRI costs $2000 elsewhere.',
    'Whole-body MRI: a $500 deposit reserves your appointment and applies to the total.',
    'MRI monthly payment $100.',
])
def test_comparisons_and_partial_payments_are_not_exam_prices(text):
    assert not extract_text(text,'https://example.com/prices',context='Self-pay MRI')


def test_own_offer_survives_comparison_and_deposit_on_same_page():
    html='''<h1>Self-pay MRI prices</h1><p>MRI without contrast $399</p>
    <p>A single MRI at regional hospitals runs $1,000–$3,000.</p>
    <p>A $100 deposit reserves your MRI appointment and applies to the total.</p>'''
    rows=[enrich(r) for r in extract_html(html,'https://example.com/prices')]
    assert [(r['amount'],r['service']) for r in rows]==[('399','MRI without contrast')]


def test_hospital_own_price_is_not_a_comparison():
    rows=extract_html('<h1>Regional Hospital self-pay prices</h1><p>MRI without contrast $500</p>', 'https://example.com/prices')
    assert len(rows)==1 and not enrich(rows[0])['extraction_issues']
    assert extract_text('Regional Hospital self-pay MRI $500','https://example.com/prices')


def test_nested_amounts_cannot_escape_comparison_or_deposit_context():
    html='''<h1>Self-pay MRI</h1><p>A single MRI at regional hospitals runs <span>$1,000</span>–<span>$3,000</span>.</p>
    <div><h2>Whole Body MRI</h2><p>Deposit applies to the total</p><span>$500</span></div>'''
    assert not extract_html(html,'https://example.com/prices')


def test_old_saved_comparison_cannot_be_rebuilt_approved_or_applied(tmp_path):
    evidence='A single MRI at regional hospitals runs $1,000–$3,000.'
    price={'id':'r','record_id':'A','service':'MRI','evidence':evidence,'source_url':'https://example.com/prices',
           'amount':'1000','currency':'USD','price_type':'self-pay / cash','modality':'MRI','qualifier':'listed','verification_status':'repeated source match'}
    assert not build_proposals({'id':'s','results':[price]},[{'id':'A','name':'Center','source':{}}])
    store=Store(tmp_path)
    p={'id':'p','kind':'price_evidence','field':'New self-pay price evidence','value':'MRI USD1000','evidence':evidence,'source_url':price['source_url']}
    store.add_proposals([p])
    with pytest.raises(ValueError,match='cannot be approved'):store.review(['p'],'approved')
    store.add_proposals([dict(p,id='already-approved',scan_id='legacy',status='approved')])
    assert store.apply(['already-approved'])[0]['status']=='blocked'
