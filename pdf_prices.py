"""Conservative layout-based PDF price panels, with line extraction as fallback."""
import re
from extraction import extract_text,MONEY
from price_quality import observation_key

LABEL=re.compile(r'^(?:MRI|MR|CT(?: SCAN| CALCIUM SC\s*ORE)?|X[- ]?RAY|ULTRASOUND)\s*$',re.I)
CONTRAST=re.compile(r'^(?:without|with(?: and without)?) contrast',re.I)

def extract_pdf_page(page,url,number):
    plain=page.extract_text() or ''
    try:layout=page.extract_text(extraction_mode='layout') or ''
    except (TypeError,ValueError):layout=''
    lines=layout.splitlines()
    table_records=[]
    for index,line in enumerate(lines):
        header=re.fullmatch(r'\s*(?:PROCEDURE|EXAM|SERVICE)\s{2,}(?P<cash>SELF[- ]?PAY|CASH PRICES?)\s*',line,re.I)
        if not header:continue
        column=header.start('cash');started=False
        for row in lines[index+1:]:
            if not row.strip():continue
            amounts=list(MONEY.finditer(row))
            if len(amounts)!=1:
                if started or amounts:break
                if re.search(r'AT TIME OF SERVICE',row,re.I):continue
                break
            amount=amounts[0]
            if amount.start()<column-8:break
            service=row[:amount.start()].strip()
            if not service or len(service)>180:break
            found=extract_text(service+' '+amount.group(),url,'pdf self-pay table',page=number,context=line.strip())
            footnote=re.match(r'\s*(\*+)',row[amount.end():])
            if footnote:
                symbol=footnote.group(1)
                note=next((text.strip() for text in lines if re.match(r'\s*'+re.escape(symbol)+r'(?!\*)\s+\S',text)), '')
                for record in found:
                    record['context']+=' | '+(note or 'Footnote '+symbol+'; verify fee scope')
            table_records.extend(found);started=True
    if table_records:return list({observation_key(r):r for r in table_records}.values()),len(plain.strip())
    anchors=[(i,m.start()) for i,line in enumerate(lines) if (m:=re.search(r'(?:CASH PRICES|SELF[- ]?PAY PRIC(?:ES|ING))',line,re.I))]
    records=[]
    for index,column in anchors:
        # Only handle a clearly separated right-hand pricing panel. Other layouts remain review-only.
        if column<20:continue
        start=max(0,column-8);service='';last=-10
        for offset,line in enumerate(lines[index+1:index+45],index+1):
            cell=line[start:].strip()
            if not cell:continue
            if LABEL.fullmatch(cell):
                service=re.sub(r'SC\s+ORE','SCORE',cell,flags=re.I);last=offset;continue
            money=list(MONEY.finditer(cell))
            if money and len(money)==1 and service and offset-last<=3:
                prefix=cell[:money[0].start()].strip(' .:-')
                if prefix and not CONTRAST.match(prefix):service='';continue
                description=service+(' '+prefix if prefix else '')
                found=extract_text(description+' '+money[0].group(),url,'pdf layout panel',page=number,context='Published cash prices panel')
                for r in found:
                    r['price_type']='self-pay / cash'
                    r['evidence']=service+' | '+cell
                    r['confidence']='needs review'
                    r['reason']='Price and exam linked within a layout-separated cash-price panel. Verify page and fee scope.'
                records.extend(found);last=offset
                continue
            if len(cell)>80 or re.search(r'PHONE|ADDRESS|SCHEDULE|SUPERVISOR',cell,re.I):break
            service=''
    if records:return list({observation_key(r):r for r in records}.values()),len(plain.strip())
    return [r for line in plain.splitlines() for r in extract_text(line,url,'pdf text',page=number)],len(plain.strip())
