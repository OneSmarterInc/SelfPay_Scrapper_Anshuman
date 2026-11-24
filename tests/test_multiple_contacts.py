import io
import zipfile
from test_plain_exports import sheet, NS
from contacts import extract_contacts
from contact_values import PHONE_FIELD, EMAIL_FIELD, values, contains
from enrichment import build_proposals
from center_view import centers
from excel_export import directory_workbook, export_value
from test_enrichment import seed


def test_extract_multiple_structured_contacts_and_extensions():
    html='''<script type="application/ld+json">{"@type":"MedicalClinic","name":"Test Imaging",
    "telephone":["212-555-0100","+1 (212) 555-0100","212-555-0100 EXT 2"],
    "email":["INFO@example.com","billing@example.com"],
    "contactPoint":{"telephone":"212-555-0100 x3","email":"appointments@example.com"}}</script>'''
    contacts,_=extract_contacts(html,'https://example.com/contact')
    assert len([c for c in contacts if c['kind']=='phone'])==3
    assert len([c for c in contacts if c['kind']=='email'])==3


def test_approve_apply_multiple_contacts_and_export_undo(tmp_path):
    store=seed(tmp_path)
    contacts=[{'record_id':'D-1','field':field,'value':value,'source_url':'https://example.com/contact','evidence':value}
        for field,value in [(PHONE_FIELD,'212-555-0100'),(PHONE_FIELD,'212-555-0200'),(PHONE_FIELD,'+1 (212) 555-0100'),
                            (EMAIL_FIELD,'INFO@example.com'),(EMAIL_FIELD,'billing@example.com')]]
    proposals=build_proposals({'id':'s','contacts':contacts},store.directory()['records'])
    assert len(proposals)==4 and all(p['kind']=='contact_add' for p in proposals)
    store.add_proposals(proposals)
    ids=[p['id'] for p in store.proposals()]
    store.review(ids,'approved')
    assert all(r['status']=='applied' for r in store.apply(ids))
    row=store.directory()['records'][0]
    assert len(values(PHONE_FIELD,row['source'][PHONE_FIELD]))==2
    assert len(values(EMAIL_FIELD,row['source'][EMAIL_FIELD]))==2
    assert not build_proposals({'id':'again','contacts':contacts},[row])
    assert all(o['label']=='Unchanged' for o in centers([row],[],[{'id':'s','contacts':contacts}])[0]['observations'])
    exported=directory_workbook(store)
    root,data=sheet(exported,1)
    assert any('212-555-0100' in str(c) and '212-555-0200' in str(c) for r in data for c in r)
    assert root.find('.//s:c[@r="F2"]',NS).get('s')=='2'
    with zipfile.ZipFile(io.BytesIO(exported)) as zipped:
        assert 'FFFFFF00' in zipped.read('xl/styles.xml').decode()
    history=store.history();store.undo(history[0]['id'])
    assert store.directory()['records'][0]['source']==history[0]['before']['source']


def test_additional_contact_keeps_existing_and_concurrent_additions(tmp_path):
    store=seed(tmp_path)
    def propose(value,scan):
        finding={'record_id':'D-1','field':PHONE_FIELD,'value':value,'source_url':'https://example.com/contact','evidence':value}
        store.add_proposals(build_proposals({'id':scan,'contacts':[finding]},store.directory()['records']))
        return next(p['id'] for p in store.proposals() if p['scan_id']==scan)
    first=propose('212-555-0100','a');second=propose('212-555-0200','b')
    store.review([first,second],'approved');store.apply([first,second])
    third=propose('212-555-0100 ext 2','c');store.review([third],'approved');store.apply([third])
    current=store.directory()['records'][0]['source'][PHONE_FIELD]
    assert len(values(PHONE_FIELD,current))==3
    assert contains(PHONE_FIELD,current,'+1 (212) 555-0100')
    assert not contains(PHONE_FIELD,current,'212-555-0100 ext 3')
    store.apply([third])
    assert store.directory()['records'][0]['source'][PHONE_FIELD]==current


def test_export_deduplicates_multivalue_cells():
    assert export_value(EMAIL_FIELD,'INFO@example.com; billing@example.com\ninfo@example.com')=='info@example.com\nbilling@example.com'
    assert len(values(PHONE_FIELD,export_value(PHONE_FIELD,'212-555-0100; 212-555-0200\n+1 (212) 555-0100')))==2
