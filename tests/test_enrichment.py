import asyncio
import csv
import io
import json
from pathlib import Path
import pytest
from contacts import extract_contacts
from enrichment import Store,build_proposals,PRICE_FIELD,missing
from directory import parse_directory


def seed(tmp_path):
    text='Center or operator,Stable source record ID,Current supported address,Current supported call number,Current usable email,Current supported offer / price scope,Official pricing / program source\nTest Imaging,D-1,,, ,Quote required,https://example.com\n'
    data=parse_directory(text)
    (tmp_path/'directory.json').write_text(json.dumps(data))
    return Store(tmp_path)


def contact(value='212-555-0100',kind='fill',field='Current supported call number'):
    return {'id':'proposal-'+value,'kind':kind,'record_id':'D-1','record_name':'Test Imaging','field':field,'value':value,'old_value':'','source_url':'https://example.com/contact','scope':'Published contact; facility scope checked in review','evidence':value,'scan_id':'test'}


def test_contacts_structured_and_visible_no_scripts():
    html='''<script type="application/ld+json">{"@type":"MedicalClinic","name":"Test Imaging","address":{"streetAddress":"12 Main St","addressLocality":"New York","addressRegion":"NY","postalCode":"10001"},"telephone":"212-555-0100","email":"appointments@example.com"}</script><script>const email="developer@bad.test"</script><p>Fax: 212-555-9999</p><a href="mailto:frontdesk@example.com">Email us</a>'''
    contacts,entities=extract_contacts(html,'https://example.com/contact')
    assert any(c['value']=='appointments@example.com' for c in contacts)
    assert not any('developer' in c['value'] or '9999' in c['value'] for c in contacts)
    assert entities[0]['address']=='12 Main St, New York, NY, 10001'


def test_apply_requires_review_preserves_original_and_restarts(tmp_path):
    store=seed(tmp_path);original=(tmp_path/'directory.json').read_bytes()
    item=contact();store.add_proposals([item])
    with pytest.raises(ValueError,match='approved'):store.apply([item['id']])
    store.review([item['id']],'approved');assert store.apply([item['id']])[0]['status']=='applied'
    assert Store(tmp_path).directory()['records'][0]['source'][item['field']]==item['value']
    assert store.apply([item['id']])[0]['status']=='already applied'
    assert len(store.history())==1
    assert (tmp_path/'directory.json').read_bytes()==original


def test_conflicting_approved_values_do_not_overwrite(tmp_path):
    store=seed(tmp_path);a=contact();b=contact('646-555-0100')
    store.add_proposals([a,b]);store.review([a['id'],b['id']],'approved')
    outcomes=store.apply([a['id'],b['id']])
    assert [r['status'] for r in outcomes]==['applied','blocked']
    assert store.directory()['records'][0]['source'][a['field']]==a['value']


def test_undo_protects_newer_edit(tmp_path):
    store=seed(tmp_path);a=contact();b=contact('12 Main Street','fill','Current supported address')
    store.add_proposals([a,b]);store.review([a['id'],b['id']],'approved');store.apply([a['id'],b['id']])
    newest,oldest=store.history()
    with pytest.raises(ValueError,match='Newer'):store.undo(oldest['id'])
    store.undo(newest['id']);store.undo(oldest['id'])
    assert not store.directory()['records'][0]['source'][a['field']]
    assert all(p['status']=='pending' for p in store.proposals())


def test_full_export_includes_provenance_and_all_rows(tmp_path):
    store=seed(tmp_path);p=contact();store.add_proposals([p]);store.review([p['id']],'approved');store.apply([p['id']])
    rows=list(csv.DictReader(io.StringIO(store.export().lstrip('\ufeff'))))
    assert len(rows)==1 and rows[0]['Stable source record ID']=='D-1'
    assert rows[0]['Current supported offer / price scope']=='Quote required'
    assert p['source_url'] in rows[0]['Enrichment provenance JSON']


def test_price_evidence_does_not_replace_scope(tmp_path):
    store=seed(tmp_path)
    job={'id':'scan','results':[{'record_id':'D-1','price_type':'self-pay / cash','service':'MRI brain without contrast','currency':'USD','amount':'499','qualifier':'listed','contrast':'without contrast','source_url':'https://example.com/pricing','evidence':'MRI $499 self-pay'}]}
    proposals=build_proposals(job,store.directory()['records']);store.add_proposals(proposals)
    p=store.proposals()[0];store.review([p['id']],'approved');store.apply([p['id']])
    row=store.directory()['records'][0]['source'];assert row['Current supported offer / price scope']=='Quote required'
    assert 'MRI brain' in row[PRICE_FIELD] and '499' in row[PRICE_FIELD]


def test_new_locations_duplicate_guard_and_undo(tmp_path):
    store=seed(tmp_path)
    entity={'name':'New Imaging','address':'123 Main St, Seattle, WA, 98101','city':'Seattle','state':'WA','url':'https://new.example.com','evidence':'Published location'}
    proposals=build_proposals({'id':'scan','entities':[entity]},store.directory()['records']);store.add_proposals(proposals);p=store.proposals()[0]
    store.review([p['id']],'approved');store.apply([p['id']]);assert len(store.directory()['records'])==2
    # Different source URL must not add the same physical location twice.
    other=dict(p,id='other',status='pending',source_url='https://new.example.com/contact')
    store.add_proposals([other]);store.review(['other'],'approved');assert store.apply(['other'])[0]['status']=='blocked'
    store.undo(store.history()[0]['id']);assert len(store.directory()['records'])==1


def test_reimport_preserves_working_edits(tmp_path):
    store=seed(tmp_path);p=contact();store.add_proposals([p]);store.review([p['id']],'approved');store.apply([p['id']])
    original=json.loads((tmp_path/'directory.json').read_text());summary=store.import_data(original)
    assert summary['conflicts_preserved']==1
    assert store.directory()['records'][0]['source'][p['field']]==p['value']


def test_missing_is_conservative():
    assert missing('Not listed') and missing('')
    assert not missing('Not offered at this location')
    assert not missing('Quote required — availability not confirmed')


def test_apply_transaction_rolls_back_invalid_batch(tmp_path):
    store=seed(tmp_path);p=contact();store.add_proposals([p]);store.review([p['id']],'approved')
    with pytest.raises(ValueError):store.apply([p['id'],'nonexistent'])
    assert not store.directory()['records'][0]['source'][p['field']]
    assert not store.history()


def test_enrichment_api_full_flow(tmp_path,monkeypatch):
    import app as module
    from fastapi.testclient import TestClient
    repository=seed(tmp_path);p=contact();repository.add_proposals([p]);monkeypatch.setattr(module,'DATA',tmp_path);monkeypatch.setattr(module,'JOBS',{})
    with TestClient(module.app) as client:
        headers={'x-app-token':client.get('/api/session').json()['token']}
        assert client.get('/api/directory').json()['summary']['rows']==1
        assert client.get('/api/enrichment').json()['counts']['pending']==1
        assert client.post('/api/enrichment/review',json={'ids':[p['id']],'status':'approved'},headers=headers).status_code==200
        response=client.post('/api/enrichment/apply',json={'ids':[p['id']]},headers=headers)
        assert response.json()['outcomes'][0]['status']=='applied'
        assert p['value'] in client.get('/api/directory/export.csv').text
        changes=client.get('/api/changes').json();assert len(changes)==1
        assert client.post('/api/changes/'+changes[0]['id']+'/undo',headers=headers).status_code==200
        assert p['value'] not in client.get('/api/directory/export.csv').text


def test_missing_price_cell_gets_scoped_offer_not_bare_amount(tmp_path):
    store=seed(tmp_path)
    price={'record_id':'D-1','price_type':'self-pay / cash','service':'MRI brain','currency':'USD','amount':'499','qualifier':'starting at','contrast':'without contrast','modality':'MRI','source_url':'https://example.com/pricing','evidence':'MRI brain without contrast starts at $499 cash'}
    proposals=build_proposals({'id':'scan','results':[price]},store.directory()['records'])
    fills=[p for p in proposals if p['kind']=='fill']
    assert len(fills)==1 and fills[0]['field']=='MRI self-pay (USD)'
    assert 'starting at' in fills[0]['value'] and 'MRI brain' in fills[0]['value']
    store.add_proposals(fills);p=store.proposals()[0];store.review([p['id']],'approved');store.apply([p['id']])
    assert '499' in store.directory()['records'][0]['source']['MRI self-pay (USD)']


def test_rejected_source_invalidates_approved_and_aggregate(tmp_path):
    store=seed(tmp_path)
    price={'id':'result-1','record_id':'D-1','price_type':'self-pay / cash','service':'MRI brain','currency':'USD','amount':'499','qualifier':'listed','modality':'MRI','source_url':'https://example.com/pricing','evidence':'MRI $499 cash'}
    proposals=build_proposals({'id':'scan','results':[price]},store.directory()['records']);store.add_proposals(proposals)
    ids=[p['id'] for p in store.proposals()];store.review(ids,'approved');store.reject_source('scan','result-1')
    assert all(p['status']=='invalidated' for p in store.proposals())
    with pytest.raises(ValueError,match='approved'):store.apply(ids)
    with pytest.raises(ValueError,match='rejected'):store.review(ids,'approved')


def test_identical_observations_remain_visible_for_second_scan(tmp_path):
    store=seed(tmp_path)
    finding={'record_id':'D-1','field':'Current supported call number','value':'212-555-0100','source_url':'https://example.com/contact','evidence':'Call us','scope':'Central contact'}
    for key in ['scan-1','scan-2']:
        store.add_proposals(build_proposals({'id':key,'contacts':[finding]},store.directory()['records']))
    assert len(store.proposals())==2
    assert {p['scan_id'] for p in store.proposals()}=={'scan-1','scan-2'}


def test_phone_formats_deduplicated_within_scan(tmp_path):
    store=seed(tmp_path)
    a={'record_id':'D-1','field':'Current supported call number','value':'+12125550100','source_url':'https://example.com/contact','evidence':'Call us','scope':'Central contact'}
    b=dict(a,value='(212) 555-0100',source_url='https://example.com')
    proposals=build_proposals({'id':'scan','contacts':[a,b]},store.directory()['records'])
    assert len(proposals)==1


def test_separate_fax_label_not_extracted_as_phone():
    contacts,_=extract_contacts('<p><span>Fax:</span><span>212-555-9999</span></p><p>Patients: 212-555-0100</p>','https://example.com')
    assert [c['value'] for c in contacts]==['212-555-0100']


def test_applied_source_requires_undo_before_rejection(tmp_path):
    store=seed(tmp_path);p=contact();p.update(source_result_ids=['r'],scan_id='scan');store.add_proposals([p]);store.review([p['id']],'approved');store.apply([p['id']])
    with pytest.raises(ValueError,match='Undo'):store.reject_source('scan','r')


def test_unstable_and_ambiguous_prices_cannot_fill_database(tmp_path):
    store=seed(tmp_path)
    p={'id':'r','record_id':'D-1','price_type':'self-pay / cash','currency':'USD','service':'MRI brain','amount':'499','modality':'MRI','qualifier':'listed','evidence':'MRI $499 cash','source_url':'https://example.com/prices','verification_status':'source changed'}
    assert build_proposals({'id':'scan','results':[p]},store.directory()['records'])==[]
    p['verification_status']='repeated source match';p['extraction_issues']=['Multiple amounts']
    assert build_proposals({'id':'scan','results':[p]},store.directory()['records'])==[]


def test_review_buttons_approve_reject_reset_and_protect_applied(tmp_path,monkeypatch):
    import app as module
    from fastapi.testclient import TestClient
    repository=seed(tmp_path)
    address=contact('12 Main St',field='Current supported address')
    phone=contact('212-555-0100')
    repository.add_proposals([address,phone])
    monkeypatch.setattr(module,'DATA',tmp_path);monkeypatch.setattr(module,'JOBS',{})
    with TestClient(module.app) as client:
        headers={'x-app-token':client.get('/api/session').json()['token']}
        ids=[address['id'],phone['id']]
        before=repository.directory()['records']
        for status in ('approved','rejected','pending','approved'):
            response=client.post('/api/enrichment/review',json={'ids':ids,'status':status},headers=headers)
            assert response.status_code==200
            assert all(p['status']==status for p in repository.proposals())
            assert repository.directory()['records']==before
        applied=client.post('/api/enrichment/apply',json={'ids':[address['id']]},headers=headers)
        assert applied.json()['outcomes'][0]['status']=='applied'
        assert client.post('/api/enrichment/review',json={'ids':[address['id']],'status':'rejected'},headers=headers).status_code==409
