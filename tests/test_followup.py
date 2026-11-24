import asyncio
import pytest
from app import ScanInput, ObservationReview, review_observation
from center_view import observation_id
from extraction import extract_html
from imaging import enrich
from contacts import extract_contacts
from test_enrichment import seed, contact


def test_limit():
    assert ScanInput().max_pages is None
    assert ScanInput(max_pages=500).max_pages == 500
    assert ScanInput(max_pages=501).max_pages == 501
    with pytest.raises(ValueError): ScanInput(max_pages=0)


def test_separate_package_policy_and_map_address():
    html = '<p>Every screening package has a clear self-pay price.</p><section><h3>Whole Spine MRI</h3><span>$999</span></section><section><h3>Whole Body MRI Complete</h3><span>$5999</span></section><a href="https://maps.app.goo.gl/example">5420 Kietzke Lane, Suite 108, Reno, NV 89511</a>'
    rows = [enrich(r) for r in extract_html(html,'https://example.com/pricing')]
    assert {(r['service'],int(r['amount'])) for r in rows} == {('Whole Spine MRI',999),('Whole Body MRI Complete',5999)}
    assert all(r['price_type']=='self-pay / cash' for r in rows)
    contacts,_=extract_contacts(html,'https://example.com/pricing')
    assert any('5420 Kietzke' in c['value'] for c in contacts)


def test_replacement_explicit_and_undo(tmp_path):
    repository=seed(tmp_path)
    a=contact();repository.add_proposals([a]);repository.review([a['id']],'approved');repository.apply([a['id']])
    b=contact('646-555-0100','conflict');b['old_value']=a['value']
    repository.add_proposals([b]);repository.review([b['id']],'approved')
    assert repository.apply([b['id']])[0]['status']=='blocked'
    repository.review([b['id']],'approved',replace_existing=True)
    assert repository.apply([b['id']])[0]['status']=='applied'
    repository.undo(repository.history()[0]['id'])
    assert repository.directory()['records'][0]['source'][a['field']]==a['value']


def test_raw_review_does_not_apply(monkeypatch,tmp_path):
    import app
    repository=seed(tmp_path)
    raw={'field':'Current usable email','value':'info@example.com','record_id':'D-1'}
    job={'id':'test','contacts':[raw]}
    monkeypatch.setattr(app,'get_job',lambda key:job)
    monkeypatch.setattr(app,'save',lambda job:None)
    monkeypatch.setattr(app,'store',lambda:repository)
    ident=observation_id(raw)
    outcome=asyncio.run(review_observation('test',ObservationReview(collection='contacts',observation_id=ident,review='accepted')))
    assert not outcome['directory_changed']
    assert observation_id(raw)==ident
    assert not repository.history()


def test_replacement_blocks_stale_value(tmp_path):
    repository=seed(tmp_path)
    a=contact();repository.add_proposals([a]);repository.review([a['id']],'approved');repository.apply([a['id']])
    b=contact('646-555-0100','conflict');b['old_value']='old value from another scan'
    repository.add_proposals([b]);repository.review([b['id']],'approved',replace_existing=True)
    assert repository.apply([b['id']])[0]['status']=='blocked'
    assert repository.directory()['records'][0]['source'][a['field']]==a['value']


def test_unrelated_cash_policy_does_not_label_packages():
    html='<p>Some services have a self-pay price.</p><section><h3>Whole Spine MRI</h3><span>$999</span></section>'
    rows=[enrich(r) for r in extract_html(html,'https://example.com/pricing')]
    assert rows[0]['price_type']!='self-pay / cash'


def test_labelled_address_and_split_map_address():
    html='<table><tr><td>Address</td><td>900 West 38th Street, Suite 100, Austin, TX 78705-1128</td></tr></table><h6><a href="https://maps.app.goo.gl/one">4007 Clarksville HW, Suite 101,</a></h6><h6><a href="https://maps.app.goo.gl/one">Nashville, TN 37218</a></h6>'
    rows,_=extract_contacts(html,'https://example.com')
    values=[r['value'] for r in rows if r['kind']=='address']
    assert '900 West 38th Street, Suite 100, Austin, TX 78705-1128' in values
    assert '4007 Clarksville HW, Suite 101, Nashville, TN 37218' in values


def test_map_address_does_not_combine_different_destinations():
    html='<a href="https://maps.app.goo.gl/one">12 Main St</a><a href="https://maps.app.goo.gl/two">Reno, NV 89511</a>'
    rows,_=extract_contacts(html,'https://example.com')
    assert not any(r['kind']=='address' for r in rows)
