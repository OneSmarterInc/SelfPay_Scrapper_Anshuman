from facility_matching import location_scope_issue
from crawler import required_details
from enrichment import build_proposals

BASE={'id':'D-1','name':'Imaging Boise','city':'Boise','scope':'Individual facility','source':{},'urls':['https://example.com/our-locations/idaho/boise/']}
OTHER='https://example.com/our-locations/utah/draper/'

def test_sibling_location_is_review_only_but_general_prices_and_own_subpages_are_allowed():
    assert location_scope_issue(BASE,OTHER)
    for url in ['https://example.com/prices','https://example.com/our-locations/','https://example.com/our-locations/idaho/boise/prices']:
        assert not location_scope_issue(BASE,url)
    assert not location_scope_issue(dict(BASE,scope='Operator network'),OTHER)

def test_other_branch_cannot_complete_center_or_fill_contacts():
    hospital={'name':BASE['name'],'record_id':BASE['id'],'url':BASE['urls'][0]}
    common={'hospital':BASE['name'],'record_id':BASE['id'],'source_url':OTHER}
    contacts=[dict(common,field=f,value='published') for f in ['Current supported address','Current supported call number']]
    price=dict(common,id='p',service='MRI without contrast',evidence='MRI without contrast $600',amount='600',currency='USD',price_type='self-pay / cash',verification_status='repeated source match',extraction_issues=[],qualifier='listed')
    job={'id':'scan','baseline_records':[BASE],'contacts':contacts,'results':[price]}
    assert required_details(job,hospital)['missing']==['price','phone','address']
    assert build_proposals(job,[BASE])==[]
