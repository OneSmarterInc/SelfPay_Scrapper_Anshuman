import base64
import io
import zipfile
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from excel_export import workbook
from scan_excel import HEADERS, read_file, validate_files, template
import app

def encoded(rows, headers=HEADERS):
    return base64.b64encode(workbook([('Providers',headers,rows,[30,50,30],{})])).decode()

def file(rows, name='providers.xlsx'):
    return SimpleNamespace(name=name,content=encoded(rows))

def test_template_contract():
    with pytest.raises(ValueError,match='at least one'):
        read_file('template.xlsx',base64.b64encode(template()).decode())
    assert read_file('ok.xlsx',encoded([['Test Imaging','https://example.com','']]))[0]['name']=='Test Imaging'

@pytest.mark.parametrize('rows,headers,message',[
    ([['Name','','']],HEADERS,'required'),
    ([['Name','https://example.com','']],['Wrong','Website','ID'],'headers'),
    ([['Name','file:///private','']],HEADERS,'http'),
])
def test_invalid_rows(rows,headers,message):
    with pytest.raises(ValueError,match=message):read_file('bad.xlsx',encoded(rows,headers))

def test_cross_file_duplicates_and_links():
    f=file([['Name','https://example.com','D-1']])
    records={'D-1':{'urls':['https://example.com/prices']}}
    assert validate_files([f],records)[0]['record_id']=='D-1'
    with pytest.raises(ValueError,match='duplicate'):validate_files([f,f],records)
    with pytest.raises(ValueError,match='does not exist'):validate_files([f],{})
    with pytest.raises(ValueError,match='host'):validate_files([f],{'D-1':{'urls':['https://other.com']}})

def test_formula_rejected():
    raw=base64.b64decode(encoded([['Name','https://example.com','']]))
    out=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source,zipfile.ZipFile(out,'w') as dest:
        for name in source.namelist():
            data=source.read(name)
            if name=='xl/worksheets/sheet1.xml':data=data.replace(b'<c r="A2"',b'<c r="A2"',1).replace(b'<is><t xml:space="preserve">Name',b'<f>1+1</f><is><t xml:space="preserve">Name')
            dest.writestr(name,data)
    with pytest.raises(ValueError,match='formulas'):read_file('bad.xlsx',base64.b64encode(out.getvalue()).decode())

def test_api_atomic_validation_and_success(monkeypatch):
    monkeypatch.setattr(app,'JOBS',{})
    monkeypatch.setattr(app,'save',lambda job:None)
    monkeypatch.setattr(app,'directory',lambda:{'records':[]})
    async def noop(job): pass
    monkeypatch.setattr(app,'run_scan',noop)
    client=TestClient(app.app)
    headers={'x-app-token':app.TOKEN}
    payload={'files':[vars(file([['Valid','https://example.com','']])),vars(file([['Invalid','','']],'bad.xlsx'))]}
    response=client.post('/api/scans',json=payload,headers=headers)
    assert response.status_code==400 and not app.JOBS
    assert 'bad.xlsx' in response.json()['detail']
    payload['files']=payload['files'][:1]
    response=client.post('/api/scans',json=payload,headers=headers)
    assert response.status_code==200
    job=app.JOBS[response.json()['id']]
    assert job['hospitals'][0]['name']=='Valid'
    assert job['hospitals'][0]['upload_row']==2
    assert client.get('/api/scans/template.xlsx').status_code==200

def test_malformed_excel_cells_rejected_cleanly():
    raw=base64.b64decode(encoded([['Name','https://example.com','']]))
    for replacement in (b'<c r="A2" t="inlineStr"/>', b'<c r="A2" t="e"><v>#REF!</v></c>', b'<c r="A99" t="inlineStr"><is><t>Name</t></is></c>'):
        import re
        out=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source,zipfile.ZipFile(out,'w') as dest:
            for name in source.namelist():
                data=source.read(name)
                if name=='xl/worksheets/sheet1.xml': data=re.sub(rb'<c r="A2".*?</c>',replacement,data)
                dest.writestr(name,data)
        with pytest.raises(ValueError):read_file('malformed.xlsx',base64.b64encode(out.getvalue()).decode())
