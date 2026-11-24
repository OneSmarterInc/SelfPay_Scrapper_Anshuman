from exam_matching import compare_exam
from facility_matching import FacilityIndex
from comparison import describe
from enrichment import Store, build_proposals
from test_enrichment import seed

def test_equal_complete_exam_changed_amount():
    a='MRI brain without contrast CPT 70551 all-inclusive $500'
    b='MRI brain without contrast CPT 70551 all-inclusive USD 600'
    result=compare_exam(a,b)
    assert result['state']=='comparable' and result['amount_changed']
    assert not compare_exam(a,a)['amount_changed']

def test_do_not_compare_different_exam_details():
    a='MRI brain without contrast CPT 70551 all-inclusive $500'
    for b,field in [('MRI knee without contrast CPT 70551 all-inclusive USD 600','body part'),('MRI brain with contrast CPT 70551 all-inclusive USD 600','contrast'),('MRI brain without contrast CPT 70552 all-inclusive USD 600','cpt'),('MRI brain without contrast CPT 70551 excludes professional fee USD 600','fees')]:
        result=compare_exam(a,b)
        assert result['state']=='different_scope' and field in result['reason']
        assert not result['amount_changed']

def test_unknown_and_bundled_baselines_not_equivalent():
    b='MRI brain without contrast all-inclusive USD 600'
    for a in ['$500','MRI brain without contrast $500','MRI brain without contrast all-inclusive $500; knee $600']:
        assert compare_exam(a,b)['state']!='comparable'
    assert compare_exam('MRI left knee without contrast all-inclusive $500','MRI right knee without contrast all-inclusive USD 600')['state']=='different_scope'

def record(key,name,address,city='Austin'):
    return {'id':key,'name':name,'city':city,'state':'TX','urls':['https://imaging.example'],'source':{'Current supported address':address}}

def test_location_aliases_normalize_addresses_not_suites():
    index=FacilityIndex([record('A','Old Imaging','123 North Main Street, Suite 200, Austin, TX 78701')])
    candidate={'name':'Renamed Imaging','address':'123 N Main St, Ste. 200, Austin, TX 78701'}
    assert index.candidates(candidate)[0]['id']=='A'
    assert not index.candidates(dict(candidate,address='123 N Main St, Suite 300, Austin, TX 78701'))
    assert not index.candidates(dict(candidate,address='456 N Main St, Austin, TX 78701'))

def test_missing_address_needs_name_and_geography_or_host():
    index=FacilityIndex([record('A','The Example Imaging LLC','')])
    assert index.candidates({'name':'Example Imaging, Inc.','city':'Austin','address':'123 Main St'})
    assert not index.candidates({'name':'Example Imaging','city':'Dallas','address':'123 Main St','url':'https://imaging.example'})
    assert not index.candidates({'name':'Different Imaging','city':'Austin','url':'https://imaging.example','address':'123 Main St'})

def test_possible_duplicates_remain_visible_and_apply_blocks(tmp_path):
    store=seed(tmp_path)
    with store.connect() as db:
        import json
        row=store.directory()['records'][0]
        row['source']['Current supported address']='123 Main Street, Austin, TX 78701'
        db.execute('UPDATE records SET body=? WHERE id=?',(json.dumps(row),row['id']))
    entity={'name':'Alias Imaging','address':'123 Main St, Austin, TX 78701','city':'Austin','state':'TX','url':'https://example.com','evidence':'Location'}
    proposals=build_proposals({'id':'new','entities':[entity]},store.directory()['records'])
    assert len(proposals)==1
    store.add_proposals(proposals);p=store.proposals()[0]
    store.review([p['id']],'approved')
    assert store.apply([p['id']])[0]['status']=='blocked'
    assert len(store.directory()['records'])==1

def test_comparison_does_not_mutate_record():
    r={'source':{'MRI self-pay (USD)':'MRI knee without contrast $500'}}
    p={'kind':'price_evidence','status':'pending','value':'MRI brain with contrast: USD 600'}
    result=describe(p,r)
    assert result['comparison_tone']=='evidence'
    assert result['exam_comparison']['state']=='different_scope'
    assert r['source']['MRI self-pay (USD)']=='MRI knee without contrast $500'


def test_enrichment_api_explains_existing_location(tmp_path,monkeypatch):
    import app
    from fastapi.testclient import TestClient
    store=seed(tmp_path)
    entity={'name':'Test Imaging LLC','address':'123 Main Street','url':'https://example.com','evidence':'Location'}
    store.add_proposals(build_proposals({'id':'new','entities':[entity]},store.directory()['records']))
    monkeypatch.setattr(app,'store',lambda:store)
    response=TestClient(app.app).get('/api/enrichment')
    assert response.status_code==200
    candidates=response.json()['proposals'][0]['duplicate_candidates']
    assert candidates[0]['id']=='D-1' and candidates[0]['reason']

def test_multiple_same_amounts_still_ambiguous():
    text='MRI brain without contrast all-inclusive $500 or MRI brain with contrast $500'
    assert compare_exam(text,'MRI brain without contrast all-inclusive USD 500')['state']!='comparable'
    assert compare_exam('MRI brain without contrast not all-inclusive $500','MRI brain without contrast all-inclusive USD 600')['state']=='different_scope'


def test_bundled_baseline_never_claims_different_scope_from_pooled_fields():
    result=compare_exam('$500 without contrast; $700 with and without contrast','MRI without contrast USD 500',modality='MRI')
    assert result['state']=='unknown'
    assert 'Unresolved' in result['reason']

def test_contrast_abbreviations():
    result=compare_exam('MRI brain without contrast all-inclusive $500','MRI brain W/O Contrast all-inclusive USD 500')
    assert result['state']=='comparable'
