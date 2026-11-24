from center_view import centers
from extraction import extract_html
from imaging import enrich
from pdf_prices import extract_pdf_page

def test_center_groups_fields_and_keeps_unchanged_observations():
    record={'id':'A','name':'Same Center','source':{'Current usable email':'old@example.com','Current supported call number':'123'}}
    proposal={'id':'p','kind':'fill','record_id':'A','record_name':'Same Center','field':'Current supported address','value':'12 Main St','old_value':'','status':'pending','scan_id':'s','source_url':'https://example.com'}
    job={'id':'s','contacts':[{'record_id':'A','field':'Current supported call number','value':'123','source_url':'https://example.com'},{'record_id':'A','field':'Current usable email','value':'new@example.com','source_url':'https://example.com'}]}
    result=centers([record],[proposal],[job])
    assert len(result)==1
    assert {o['label'] for o in result[0]['observations']}=={'Blank → New','Unchanged','Additional contact'}
    assert result[0]['proposal_ids']==['p']
    assert record['source']['Current usable email']=='old@example.com'

def test_same_name_separate_records_and_scan_filter():
    records=[{'id':x,'name':'Same','source':{}} for x in ['A','B']]
    jobs=[{'id':scan,'contacts':[{'record_id':rid,'field':'Phone','value':'123','source_url':'https://example.com'}]} for scan,rid in [('s1','A'),('s2','B')]]
    assert len(centers(records,[],jobs))==2
    assert centers(records,[],jobs,'s1')[0]['record_id']=='A'

def test_new_location_details_stay_together_without_merging_branches():
    entity={'name':'New','address':'1 Main St','city':'Austin','state':'TX','url':'https://example.com'}
    proposal={'id':'p','kind':'new_record','value':entity,'status':'pending','scan_id':'s','source_url':entity['url']}
    groups=centers([], [proposal],[{'id':'s','entities':[entity,dict(entity,address='2 Main St')]}])
    assert len(groups)==2
    assert len(groups[0]['observations'])==3

def test_multilevel_table_keeps_selfpay_and_insurance_separate():
    html='<h1>MRI prices</h1><table><tr><th rowspan="2">Exam</th><th colspan="2">Our center</th><th>Hospital allowable</th></tr><tr><th>Self Pay</th><th>Insurance allowable</th><th>Facility fee only</th></tr><tr><td>MRI brain without contrast</td><td>$400</td><td>$600</td><td>$3200</td></tr></table>'
    rows=[enrich(r) for r in extract_html(html,'https://example.com')]
    eligible=[r for r in rows if r['price_type']=='self-pay / cash' and not r['extraction_issues']]
    assert [r['amount'] for r in eligible]==['400']
    assert len(rows)==3 and rows[0]['service']=='MRI brain without contrast'

class Page:
    def __init__(self,layout):self.layout=layout
    def extract_text(self,**kwargs):return self.layout

def test_pdf_panel_preserves_page_and_contrast():
    page=Page('Services                               CASH PRICES\n                                       MRI\n                                       without contrast... $500\n                                       with and without contrast... $600\n                                       CT SCAN\n                                       without contrast... $350\n                                       PHONE NUMBERS\n                                       $99')
    rows,chars=extract_pdf_page(page,'https://example.com/list.pdf',2)
    assert [r['amount'] for r in rows]==['500','600','350']
    assert all(r['page']==2 and r['price_type']=='self-pay / cash' for r in rows)
    assert [enrich(r)['contrast'] for r in rows]==['without contrast','without and with contrast','without contrast']

def test_pdf_does_not_attach_unrelated_amounts_or_other_column():
    page=Page('Other                                  CASH PRICES\nMRI $999                               MRI\n                                       Please call to schedule\n                                       $500')
    rows,_=extract_pdf_page(page,'https://example.com/list.pdf',1)
    assert not any(r['amount']=='500' for r in rows)


def test_repeated_contact_values_keep_all_sources_in_one_field():
    job={'id':'s','contacts':[{'record_id':'A','field':'Current supported call number','value':v,'source_url':url,'crawl_route':[{'url':url,'via':'Contact link'}]} for v,url in [('212-555-1234','https://example.com/a'),('(212) 555-1234','https://example.com/b')]]}
    g=centers([{'id':'A','name':'Center','source':{}}],[],[job])[0]
    assert len(g['observations'])==1
    assert len(g['observations'][0]['sources'])==2
    assert g['observations'][0]['sources'][1]['crawl_route'][0]['via']=='Contact link'


def test_equivalent_proposals_group_with_all_actions_and_sources():
    record={'id':'A','name':'Center','source':{}}
    base={'id':'p1','kind':'fill','record_id':'A','field':'Current supported call number','value':'(775) 507-3331','old_value':'','status':'pending','scan_id':'one','source_url':'https://example.com/a'}
    second=dict(base,id='p2',value='+17755073331',scan_id='two',source_url='https://example.com/b')
    different=dict(base,id='p3',value='775-507-9999')
    rejected=dict(base,id='p4',status='rejected')
    observations=centers([record],[base,second,different,rejected],[])[0]['observations']
    assert len(observations)==3
    assert observations[0]['proposal_ids']==['p1','p2']
    assert len(observations[0]['sources'])==2
    assert observations[0]['scan_ids']==['one','two']
    assert len(centers([record],[base,second],[],'two')[0]['observations'])==1


def test_duplicate_prices_keep_different_exam_scope_separate():
    record={'id':'A','name':'Center','source':{}}
    base={'id':'p1','kind':'price_evidence','record_id':'A','field':'New self-pay price evidence','value':'MRI: USD 399','old_value':'','status':'pending','scan_id':'one','exam_scope':{'contrast':'without contrast'}}
    duplicate=dict(base,id='p2',source_url='https://example.com/b')
    different=dict(base,id='p3',exam_scope={'contrast':'with contrast'})
    rows=centers([record],[base,duplicate,different],[])[0]['observations']
    assert len(rows)==2 and rows[0]['proposal_ids']==['p1','p2']

def test_contact_equivalence_preserves_suite_and_extension():
    from facility_matching import field_key
    assert field_key('Current supported call number','+1 (775) 507-3331')==field_key('Current supported call number','775-507-3331')
    assert field_key('Current supported call number','775-507-3331 ext 2')!=field_key('Current supported call number','775-507-3331 ext 3')
    assert field_key('Current supported address','12 Main Street, Suite 108')==field_key('Current supported address','12 Main St Ste 108')
    assert field_key('Current supported address','12 Main St Ste 108')!=field_key('Current supported address','12 Main St Ste 109')


def test_raw_duplicate_is_replaced_by_actionable_evidence_with_sources():
    record={'id':'A','name':'Center','source':{}}
    proposal={'id':'p','kind':'fill','record_id':'A','field':'Current supported call number','value':'775-507-3331','old_value':'','status':'pending','scan_id':'one','source_url':'https://example.com/a'}
    job={'id':'two','contacts':[{'record_id':'A','field':proposal['field'],'value':'+1 (775) 507-3331','source_url':'https://example.com/b'}]}
    group=centers([record],[proposal],[job])[0]
    assert len(group['observations'])==1
    assert len(group['observations'][0]['sources'])==2


def test_address_abbreviations_collapse_but_different_suites_do_not():
    record={'id':'A','name':'Center','source':{}}
    base={'id':'p1','kind':'fill','record_id':'A','field':'Current supported address','value':'12 Main Street Suite 108','old_value':'','status':'pending','scan_id':'one','source_url':'https://example.com/a'}
    rows=[base,dict(base,id='p2',value='12 Main St Ste 108'),dict(base,id='p3',value='12 Main St Ste 109')]
    group=centers([record],rows,[])[0]
    assert len(group['observations'])==2
    assert group['observations'][0]['proposal_ids']==['p1','p2']
