import io
import zipfile
import xml.etree.ElementTree as ET
from comparison import describe, signals
from excel_export import workbook

def test_price_comparison_does_not_claim_equivalent_scope():
    p={'kind':'price_evidence','status':'pending','value':'MRI brain: USD 600','old_value':''}
    result=describe(p,{'source':{'MRI self-pay (USD)':'$500'}})
    assert result['comparison_tone']=='evidence'
    assert result['exam_comparison']['state']=='unknown'
    assert result['comparison_old']=='$500'
    assert describe(p,{'source':{'MRI self-pay (USD)':'600'}})['comparison_tone']=='evidence'

def test_undone_fill_has_no_applied_highlight():
    change={'undone':True,'record_id':'a','field':'Email','before':{'source':{'Email':''}},'after':{'name':'Hospital','source':{'Email':'hello@example.com'}}}
    assert signals([],[],[change])=={}
    change['undone']=False
    assert signals([],[],[change])['a'][0]['comparison_tone']=='filled'

def test_xlsx_literal_cells_filters_and_highlights():
    raw=workbook([('Updates',['Provider','Before','Found'],[['Hospital','','=HYPERLINK("bad")']], [30,40,40],{(0,2):2})])
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for path in z.namelist():ET.fromstring(z.read(path))
        root=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        cell=root.find('.//s:c[@r="C2"]',ns)
        assert cell.attrib['t']=='inlineStr' and cell.attrib['s']=='2'
        assert cell.find('s:f',ns) is None
        assert root.find('s:autoFilter',ns).attrib['ref']=='A1:C2'
        assert root.find('.//s:pane',ns).attrib['ySplit']=='1'

def test_new_location_audit_shows_created_value():
    from comparison import audit_values
    a={'field':'New location','before':None,'after':{'name':'Imaging A','source':{'Current supported address':'123 Main St'}}}
    assert audit_values(a)['new_value']=='Imaging A - 123 Main St'
    assert audit_values(a)['change_label']=='New location added'

def test_scan_excel_preserves_currency(monkeypatch):
    import app
    monkeypatch.setattr(app,'JOBS',{'x':{'results':[{'amount':'123','currency':'CAD'}]}})
    response=app.export_scan_excel('x')
    with zipfile.ZipFile(io.BytesIO(response.body)) as z:
        xml=z.read('xl/worksheets/sheet1.xml').decode()
        assert 'Currency' in xml and 'CAD' in xml and 'Published amount (USD)' not in xml
