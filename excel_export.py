"""Small local XLSX exporter: literal text only, no macros, formulas or remote dependencies."""

import io
import math

import re

import zipfile

from xml.sax.saxutils import escape

from comparison import describe, signals, audit_values
from facility_matching import field_key
import json
from urllib.parse import urlsplit



MIME='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'





def column(n):

    out=''

    while n:

        n,r=divmod(n-1,26);out=chr(65+r)+out

    return out





def text(value):

    return escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value if value is not None else ''))[:32767])





def readable_rows(rows,widths,highlights):
    """Continue exceptionally long rows instead of clipping them at Excel's height limit."""
    import textwrap
    expanded=[];marks={}
    for original,values in enumerate(rows):
        chunks=[]
        for col,value in enumerate(values):
            value=str(value if value is not None else '')
            width=max(8,int(widths[col])-3)
            if sum(max(1,math.ceil(len(line)/width)) for line in value.split('\n'))<=20:
                chunks.append([value]);continue
            parts=[];current=[];used=0
            for paragraph in value.split('\n'):
                for segment in textwrap.wrap(paragraph,width=width*18,break_long_words=False,break_on_hyphens=False) or ['']:
                    count=max(1,math.ceil(len(segment)/width))
                    if current and used+count>20:
                        parts.append('\n'.join(current));current=[];used=0
                    current.append(segment);used+=count
            if current:parts.append('\n'.join(current))
            chunks.append(parts)
        for part in range(max(map(len,chunks),default=1)):
            row=[parts[part] if part<len(parts) else '' for parts in chunks]
            if part and len(row)>1:row[0]=str(values[0]);row[1]=str(values[1])+' (continued)'
            n=len(expanded);expanded.append(row)
            for col in range(len(values)):
                if (original,col) in highlights and row[col]:marks[n,col]=highlights[original,col]
    return expanded,marks


def workbook(sheets):

    buffer=io.BytesIO()

    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'

    styles=f'''<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/><color rgb="FF000000"/></font><font><b/><sz val="11"/><name val="Calibri"/><color rgb="FF000000"/></font></fonts><fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFFFFF"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFFF00"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFFF00"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''

    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:

        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))+'</Types>')

        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')

        z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{text(s[0])}" sheetId="{i}" r:id="rId{i}"/>' for i,s in enumerate(sheets,1))+'</sheets></workbook>')

        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))+f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')

        z.writestr('xl/styles.xml',styles)

        for i,(name,headers,rows,widths,highlights) in enumerate(sheets,1):

            rows,highlights=readable_rows(rows,widths,highlights)
            body=[]

            for r,values in enumerate([headers]+rows,1):

                cells=[]

                for c,value in enumerate(values,1):

                    style=1 if r==1 else highlights.get((r-2,c-1),0)

                    cells.append(f'<c r="{column(c)}{r}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text(value)}</t></is></c>')

                height=30 if r==1 else min(409,max(18,16*max(sum(max(1,math.ceil(len(line)/max(8,widths[j]-3))) for line in str(v).split('\n')) for j,v in enumerate(values))))
                body.append(f'<row r="{r}" ht="{height}" customHeight="1">'+''.join(cells)+'</row>')

            end=f'{column(len(headers))}{len(rows)+1}'

            z.writestr(f'xl/worksheets/sheet{i}.xml',f'<worksheet xmlns="{ns}"><dimension ref="A1:{end}"/><sheetViews><sheetView workbookViewId="0"><pane xSplit="2" ySplit="1" topLeftCell="C2" activePane="bottomRight" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/><cols>'+''.join(f'<col min="{n}" max="{n}" width="{w}" customWidth="1"/>' for n,w in enumerate(widths,1))+'</cols><sheetData>'+''.join(body)+f'</sheetData><autoFilter ref="A1:{end}"/></worksheet>')

    return buffer.getvalue()





def joined(values):
    """Keep distinct values in source order, including different review states."""
    return '\n'.join(dict.fromkeys(str(v) for v in values if v not in ('', None)))


def export_value(field,value):
    if field not in ('Current supported call number','Current usable email','New self-pay price evidence'):
        return value
    seen=set();lines=[]
    for line in str(value or '').splitlines():
        display=re.split(r'\s+\|\s+https?://',line,maxsplit=1)[0] if field=='New self-pay price evidence' else line
        key=field_key(field,display)
        if key not in seen and display.strip():seen.add(key);lines.append(display)
    return '\n'.join(lines)


def directory_workbook(store):
    records=store.directory()['records'];proposals=store.proposals();history=store.history()
    marks=signals(records,proposals,history);index={r['id']:r for r in records}
    fields=['Current supported address','Current supported call number','Current usable email','MRI self-pay (USD)','CT self-pay (USD)','Current supported offer / price scope','New self-pay price evidence']
    labels=['Address','Phone','Email','MRI self-pay (USD)','CT self-pay (USD)','Price scope','Additional price evidence']
    headers=['Record ID','Provider','State','City / area']+labels+['Applied changes: old value','Applied changes: new value','Source URL']
    rows=[];highlights={};duplicates={}
    for r in records:
        # Deduplicate identical records, never names alone.
        identity=json.dumps([r['name'],r.get('state',''),r.get('city',''),{k:v for k,v in r['source'].items() if k not in ('Stable source record ID','Enrichment provenance JSON')}],sort_keys=True)
        if identity in duplicates:
            n=duplicates[identity];rows[n][0]=joined([rows[n][0],r['id']])
        else:
            n=len(rows);duplicates[identity]=n
            rows.append([r['id'],r['name'],r.get('state',''),r.get('city','')]+[export_value(f,r['source'].get(f,'')) for f in fields]+['','',''])
        applied=[a for a in reversed(history) if a.get('record_id')==r['id'] and not a.get('undone')]
        old=[];new=[]
        for a in applied:
            values=audit_values(a);field=a['field']
            old.append(field+': '+(str(export_value(field,values['old_value'])) or 'Blank / not recorded'))
            new.append(field+': '+str(export_value(field,values['new_value'])))
            if field in fields:highlights[n,4+fields.index(field)]=2
        rows[n][11]=joined([rows[n][11],joined(old)])
        rows[n][12]=joined([rows[n][12],joined(new)])
        provenance=json.loads(r['source'].get('Enrichment provenance JSON') or '[]')
        rows[n][13]=joined([rows[n][13],*r.get('urls',[]),*[e.get('source_url','') for e in provenance]])
        if applied:
            highlights[n,11]=highlights[n,12]=2
    grouped={}
    for raw in proposals:
        if raw['status'] in ('rejected','invalidated'):continue
        p=describe(raw,index.get(raw.get('record_id')))
        value=p['value'];rid=p.get('applied_record_id') or p.get('record_id','')
        key=('record',rid) if rid else ('location',p.get('record_name',''),json.dumps(value,sort_keys=True) if isinstance(value,dict) else p.get('source_url',''))
        g=grouped.setdefault(key,{'id':rid,'name':p.get('record_name',''),'changes':[]})
        if isinstance(value,dict):value=value.get('name','')+' â€” '+value.get('address','')
        old=p['comparison_old'] or 'Blank / not recorded'
        g['changes'].append((p['field'],old,str(value),p['status'],p.get('source_url',''),p['comparison_label']))
    updates=[];colors={}
    for g in grouped.values():
        unique={}
        for field,old,new,state,url,label in g['changes']:
            key=(field,field_key(field,old),field_key(field,new))
            if key not in unique:unique[key]=[field,old,new,state,[url],label]
            else:
                item=unique[key];item[4].append(url)
                if {'pending':0,'approved':1,'applied':2}.get(state,0)>{'pending':0,'approved':1,'applied':2}.get(item[3],0):
                    item[1:4]=[old,new,state];item[5]=label
        changes=[(field,old,new,state,joined(urls),label) for field,old,new,state,urls,label in unique.values()]
        # Numbers link alternatives to their status and source.
        columns=[[],[],[],[],[]]
        for number,(field,old,new,state,url,label) in enumerate(changes,1):
            prefix=str(number)+'. '+field+': '
            columns[0].append(prefix+old);columns[1].append(prefix+new)
            columns[2].append(str(number)+'. '+state)
            columns[3].append(str(number)+'. '+url)
            columns[4].append(str(number)+'. '+label)
        n=len(updates);updates.append([g['id'],g['name']]+[joined(c) for c in columns])
        if any(old!=new for _,old,new,_,_,_ in changes):colors[n,2]=colors[n,3]=2
    return workbook([('Directory',headers,rows,[18,34,12,24,42,22,32,40,40,55,55,60,60,60],highlights),
                     ('Updates',['Record ID','Provider','Old value','New value','Review state','Source URL','Comparison'],updates,[18,34,65,65,25,65,50],colors)])


def scan_workbook(job,accepted_only=False):
    groups={}
    for r in job.get('results',[]):
        if accepted_only and r.get('review')!='accepted':continue
        rid=r.get('record_id','');name=r.get('hospital','')
        key=('record',rid) if rid else ('provider',name,urlsplit(r.get('source_url','')).hostname)
        g=groups.setdefault(key,{'id':rid,'name':name,'items':{}})
        identity=tuple(str(r.get(k,'')) for k in ('service','amount','currency','qualifier','contrast','cpt','review','baseline_scope','verification_status'))
        item=g['items'].setdefault(identity,{'row':r,'sources':[]})
        item['sources'].append(r.get('source_url',''))
    rows=[];colors={}
    for g in groups.values():
        old=[];new=[];currencies=[];states=[];sources=[];checks=[]
        for number,item in enumerate(g['items'].values(),1):
            r=item['row'];prefix=str(number)+'. '
            old.append(prefix+(r.get('baseline_scope') or 'Blank / not recorded'))
            details=joined([r.get(k,'') for k in ('qualifier','contrast','cpt')]).replace('\n','; ')
            new.append(prefix+str(r.get('service','Exam not identified'))+': '+str(r.get('currency',''))+' '+str(r.get('amount',''))+(' ('+details+')' if details else ''))
            currencies.append(r.get('currency',''));states.append(prefix+r.get('review','unreviewed'))
            sources.append(prefix+joined(item['sources']));checks.append(prefix+r.get('verification_status','Not checked'))
        n=len(rows);rows.append([g['id'],g['name'],joined(old),joined(new),joined(currencies),joined(states),joined(sources),joined(checks)])
        colors[n,2]=colors[n,3]=2
    return workbook([('Price evidence',['Record ID','Provider','Old price scope','New published offers','Currency','Review state','Source URL','Source check'],rows,[18,34,60,70,12,25,65,35],colors)])
