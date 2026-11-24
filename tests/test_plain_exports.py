import io
import zipfile
import xml.etree.ElementTree as ET
from excel_export import directory_workbook,scan_workbook
from test_enrichment import seed,contact
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def sheet(data,n):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root=ET.fromstring(z.read(f'xl/worksheets/sheet{n}.xml'))
        return root,[[c.find('s:is/s:t',NS).text or '' for c in row] for row in root.findall('s:sheetData/s:row',NS)]

def test_plain_yellow_grouped_and_applied_comparison(tmp_path):
    store=seed(tmp_path)
    a=contact();b=contact('frontdesk@example.com',field='Current usable email')
    duplicate=dict(a,id='copy',source_url='https://example.com/other')
    store.add_proposals([a,b,duplicate])
    data=directory_workbook(store)
    _,updates=sheet(data,2)
    assert len(updates)==2
    assert updates[1][3].count(a['value'])==1
    assert 'other' in updates[1][5]
    _,directory=sheet(data,1)
    assert not directory[1][11] and not directory[1][12]
    store.review([a['id']],'approved');store.apply([a['id']])
    data=directory_workbook(store)
    root,directory=sheet(data,1)
    assert 'Blank / not recorded' in directory[1][11]
    assert a['value'] in directory[1][12]
    assert root.find('.//s:c[@r="F2"]',NS).get('s')=='2'
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        styles=z.read('xl/styles.xml').decode()
        assert 'FFFFFF00' in styles
        assert 'FFDCEBFA' not in styles and 'FFEAE0F6' not in styles
        style=ET.fromstring(styles)
        assert style.find('s:cellXfs',NS)[1].get('fillId')=='0'
    store.undo(store.history()[0]['id'])
    _,directory=sheet(directory_workbook(store),1)
    assert not directory[1][11] and not directory[1][12]

def test_scan_groups_same_client_preserves_different_offers_and_branches():
    a={'record_id':'D-1','hospital':'Imaging','service':'MRI','amount':'399','currency':'USD','review':'accepted','source_url':'https://example.com/a'}
    b=dict(a,source_url='https://example.com/b')
    c=dict(a,amount='499',contrast='with contrast')
    d=dict(a,record_id='D-2')
    _,rows=sheet(scan_workbook({'results':[a,b,c,d]}),1)
    assert len(rows)==3
    assert rows[1][3].count('399')==1 and '499' in rows[1][3]
    assert 'with contrast' in rows[1][3]
    assert 'https://example.com/b' in rows[1][6]

def test_api_approve_apply_export_updated_yellow_and_unique(tmp_path,monkeypatch):
    import app
    from fastapi.testclient import TestClient
    store=seed(tmp_path)
    a=contact('212-555-0100')
    duplicate=dict(a,id='duplicate',value='+1 (212) 555-0100',scan_id='other',source_url='https://example.com/other')
    store.add_proposals([a,duplicate])
    monkeypatch.setattr(app,'DATA',tmp_path);monkeypatch.setattr(app,'JOBS',{})
    with TestClient(app.app) as client:
        headers={'x-app-token':client.get('/api/session').json()['token']}
        assert client.post('/api/enrichment/review',json={'ids':[a['id']],'status':'approved'},headers=headers).status_code==200
        _,before=sheet(client.get('/api/directory/export.xlsx').content,1)
        assert before[1][5]==''
        assert client.post('/api/enrichment/apply',json={'ids':[a['id']]},headers=headers).json()['outcomes'][0]['status']=='applied'
        data=client.get('/api/directory/export.xlsx').content
        root,rows=sheet(data,1)
        assert len(rows)==2 and rows[1][5]=='212-555-0100'
        assert 'Blank / not recorded' in rows[1][11] and '212-555-0100' in rows[1][12]
        for cell in ('F2','L2','M2'):assert root.find('.//s:c[@r="'+cell+'"]',NS).get('s')=='2'
        _,updates=sheet(data,2)
        assert len(updates)==2 and updates[1][3].count('Current supported call number:')==1
        assert 'applied' in updates[1][4] and 'https://example.com/other' in updates[1][5]
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            styles=ET.fromstring(z.read('xl/styles.xml'))
            fill=int(styles.find('s:cellXfs',NS)[2].get('fillId'))
            assert styles.find('s:fills',NS)[fill].find('s:patternFill/s:fgColor',NS).get('rgb')=='FFFFFF00'


def test_export_duplicate_price_metadata_preserves_distinct_offers():
    from excel_export import export_value
    value='MRI without contrast: USD 399 | https://example.com/a | retrieved yesterday\nMRI without contrast: USD 399 | https://example.com/b | retrieved today\nMRI with contrast: USD 499 | https://example.com/c | retrieved today'
    exported=export_value('New self-pay price evidence',value)
    assert exported.count('USD 399')==1 and 'USD 499' in exported
