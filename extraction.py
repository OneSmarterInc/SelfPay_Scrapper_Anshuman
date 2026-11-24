"""Conservative, evidence-first extraction. Never guess missing currencies."""
from price_quality import source_issues, observation_key, qualify
import hashlib
import re
from decimal import Decimal, InvalidOperation
from bs4 import BeautifulSoup

CURRENCY = r"(?:₹|INR|Rs\.?|USD|US\$|\$|GBP|£|EUR|€|AED|AUD|CAD)"
NUMBER = r"\d+(?:,\d{2,3})*(?:\.\d{1,2})?"
MONEY = re.compile(rf"(?<!\w)(?P<currency>{CURRENCY})\s*(?P<amount>{NUMBER})(?!\d|\.\d)", re.I)
SUFFIX = re.compile(rf"(?<![\w.])(?P<amount>{NUMBER})\s*(?P<currency>INR|USD|GBP|EUR|AED|AUD|CAD)\b", re.I)
SERVICE = re.compile(r"mammogra|DEXA|DXA|PET\b|radiograph|sonograph|pricing|test|screen|scan|consult|package|check.?up|room|bed|surgery|procedure|echocardiogram|arthrogram|MR\b|treatment|dialysis|MRI|CTA?\b|blood|health|cardiac|delivery|vaccin|ultrasound|x.?ray|ECG|panel|profile|therapy|tariff|charge|price|cost|fee", re.I)


def normalize_currency(value):
    return {"₹": "INR", "RS": "INR", "RS.": "INR", "US$": "USD", "£": "GBP", "€": "EUR", "$": "unspecified dollar"}.get(value.upper(), value.upper())


def extract_text(text, url, method="text", page=None, context="", currency_hint=None):
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > 2500 or not SERVICE.search(text + " " + context):
        return []
    matches = list(MONEY.finditer(text)) + list(SUFFIX.finditer(text))
    matches.sort(key=lambda m: m.start())
    # A currency in a table header is evidence; an operator default is not.
    if not matches and currency_hint:
        matches = list(re.finditer(rf"(?<![\w.])(?P<amount>{NUMBER})(?![\w.])", text))
    records = []
    if source_issues(url,text):
        return []
    for match in matches:
        try:
            amount = Decimal(match.group("amount").replace(",", ""))
        except InvalidOperation:
            continue
        if amount <= 0:
            continue
        currency = normalize_currency(match.groupdict().get("currency") or currency_hint)
        before = text[:match.start()].strip(" :|-–")
        after = text[match.end():].strip(" :|-–")
        label = before if re.search(r"[A-Za-z]{3}", before) else after if SERVICE.search(after) else context
        qualifier = "listed"
        if re.search(r"\b(from|starting|onwards|minimum|starts)\b", text, re.I):
            qualifier = "starting at"
        if re.search(r"\b(up to|range|between)\b|\d\s*[-–]\s*(?:" + CURRENCY + r")?\s*\d", text, re.I):
            qualifier = "range / multiple amounts"
        if re.search(r"\b(was|original|regular|discount|special offer|limited offer|save|off)\b", text, re.I):
            qualifier = "offer / original price — review"
        reason = "Explicit currency and service text in the same block."
        confidence = "strong evidence"
        if len(matches) > 1 or qualifier != "listed" or currency == "unspecified dollar" or not before or currency_hint or method == "pdf text":
            confidence = "needs review"
            reason = "Check the service association, currency, price qualifier and source context."
        key = f"{url}|{page}|{text}|{match.start()}|{amount}|{currency}"
        records.append({"id": hashlib.sha256(key.encode()).hexdigest()[:20], "service": label[:220] or "Unresolved service", "amount": str(amount), "currency": currency, "qualifier": qualifier, "evidence": text, "context": context[:500], "source_url": url, "page": page, "method": method, "confidence": confidence, "reason": reason, "review": "unreviewed", "extraction_issues": (["Multiple amounts in the same text block; service association requires review"] if len(matches)>1 else [])})
    for record in records:
        if not SERVICE.search(text):
            record['extraction_issues'].append('Amount lacks an exam label in its own text block')
            record['confidence']='needs review'
        if re.search(r'\b(deposit|monthly|per month|cancellation|late fee|coupon|save \$|savings)\b',text,re.I):
            record['extraction_issues'].append('May be a deposit, financing amount, saving or administrative fee rather than an exam price')
            record['confidence']='needs review'
    return records


def section_context(element):
    headings=[]
    sibling=element.find_previous_sibling()
    if sibling is not None:
        value=sibling.get_text(' ',strip=True)
        if len(value)<=100 and re.search(r'self[- ]?pay|cash pric',value,re.I) and not MONEY.search(value):headings.append(value)
    level=7
    for heading in element.find_all_previous(['h1','h2','h3','h4']):
        value=heading.get_text(' ',strip=True)
        rank=int(heading.name[1])
        if value and rank < level and not MONEY.search(value) and not SUFFIX.search(value):
            headings.append(value);level=rank
        if rank==1:break
    return ' | '.join(dict.fromkeys(headings))[:500]


def table_records(table,url):
    grid=[];spans={}
    for ri,row in enumerate(r for r in table.select('tr') if r.find_parent('table') is table):
        cols={c:cell for c,(end,cell) in spans.items() if end>ri}
        cursor=0
        for cell in row.find_all(['th','td'],recursive=False):
            while cursor in cols:cursor+=1
            try:width=int(cell.get('colspan',1));height=int(cell.get('rowspan',1))
            except ValueError:return []
            if not 1<=width<=50 or not 1<=height<=50:return []
            for col in range(cursor,cursor+width):
                cols[col]=cell
                if height>1:spans[col]=(ri+height,cell)
            cursor+=width
        grid.append([cols.get(c) for c in range(max(cols,default=-1)+1)])
    if not grid:return []
    headers=[];data_start=0
    for row in grid:
        values=[c.get_text(' ',strip=True) if c else '' for c in row]
        if any(MONEY.search(v) or SUFFIX.search(v) or re.fullmatch(NUMBER,v) for v in values):break
        headers.append(values);data_start+=1
    if not headers:return [r for row in grid for r in extract_text(' | '.join(c.get_text(' ',strip=True) for c in row if c),url,'table',context=section_context(table))]
    records=[]
    for row in grid[data_start:]:
        names=[' | '.join(dict.fromkeys(h[i] for h in headers if i<len(h) and h[i])) for i in range(len(row))]
        descriptions=[i for i,n in enumerate(names) if re.search(r'description|exam|procedure|service|item|test|type',n,re.I) and not re.search(r'code|price|charge|rate|fee|amount',n,re.I)]
        di=descriptions[0] if descriptions else 0
        if not row[di]:continue
        service=row[di].get_text(' ',strip=True)
        for i,cell in enumerate(row):
            if not cell or i==di or re.search(r'CPT|HCPCS|code|quantity',names[i],re.I):continue
            value=cell.get_text(' ',strip=True);header=names[i]
            hint=re.search(CURRENCY,header,re.I)
            code_context=' | '.join(names[k]+': '+c.get_text(' ',strip=True) for k,c in enumerate(row) if c and re.search(r'CPT|HCPCS',names[k],re.I))
            context=section_context(table)+' | '+header+' | '+code_context
            if not (MONEY.search(value) or SUFFIX.search(value) or hint and re.fullmatch(NUMBER,value)):continue
            for record in extract_text(service+' | '+value,url,'table',context=context,currency_hint=hint.group() if hint else None):
                record['service']=service
                record['evidence']=service+' | '+header+' | '+value
                if re.search(r'insurance|allowable|hospital comparison|negotiated|payer',header,re.I) and not re.search(r'self[- ]?pay|cash',header,re.I):
                    record['price_type']='comparison / insurance rate — not self-pay'
                    record['extraction_issues'].append('Comparison column is not the provider self-pay price')
                records.append(record)
    return records


def extract_html(html, url):
    if source_issues(url, ""):
        return []
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select("script,style,noscript,nav,footer,template,[hidden],[aria-hidden=true],del,s"):
        el.decompose()
    for header in soup.select('header'):
        if not header.find('h1'):header.decompose()
    for el in list(soup.find_all(True)):
        if el.parent is not None and re.search(r'(?:^|[ _-])(?:testimonial|testimonials|patient-review|customer-review)(?:$|[ _-])', ' '.join(el.get('class',[]))+' '+str(el.get('id','')),re.I):
            el.decompose()
    policies=[]
    for block in soup.find_all(['p','h1','h2']):
        value=block.get_text(' ',strip=True)
        if len(value)<600 and re.search(r'(?:every|all|our)\s+(?:screening\s+|imaging\s+)?packages?.{0,70}self[- ]?pay',value,re.I) and not re.search(r'\b(?:not|unavailable)\b',value,re.I):
            policies.append(value)
    records = []
    for table in soup.select('table'):
        if not table.find_parent('table'):records.extend(table_records(table,url))
    for row in soup.select("table"):
        row.decompose()
    # Use the smallest priced block.
    candidates = soup.find_all(["p", "li", "div", "section", "article", "h1", "h2", "h3", "h4", "span"])
    priced = [el for el in candidates if MONEY.search(el.get_text(" ", strip=True)) or SUFFIX.search(el.get_text(" ", strip=True))]
    for el in priced:
        if any(child in priced for child in el.find_all(["p", "li", "div", "section", "article", "h1", "h2", "h3", "h4", "span"])):
            continue
        heading = next((h for h in el.find_all_previous(["h1", "h2", "h3", "h4"]) if not MONEY.search(h.get_text(" ",strip=True)) and not SUFFIX.search(h.get_text(" ",strip=True))), None)
        # Match standalone amounts to their own card heading.
        card=None
        for parent in el.parents:
            if parent.name not in ('div','article','section','li'):continue
            headings=parent.find_all(['h2','h3','h4'])
            value=parent.get_text(' ',strip=True)
            if len(value)>2500:break
            if len(headings)==1 and len(list(MONEY.finditer(value))+list(SUFFIX.finditer(value)))==1:
                card=parent;break
        value=el.get_text(' ',strip=True)
        if card is not None and re.fullmatch(r"\s*(?:"+CURRENCY+r")\s*"+NUMBER+r"\s*",value,re.I):
            title=card.find(['h2','h3','h4']).get_text(' ',strip=True)
            context=card.get_text(' ',strip=True)+' | '+' | '.join(policies)
            for item in extract_text(title+' '+value,url,'price card',context=context):
                item['service']=title
                item['context']=context[:1500]
                records.append(item)
        else:
            if not SERVICE.search(value):
                for parent in el.parents:
                    if parent.name in ('ul','ol','section','article','body'):break
                    if parent.name not in ('li','p','div'):continue
                    row=parent.get_text(' ',strip=True)
                    if len(row)>350 or len(list(MONEY.finditer(row))+list(SUFFIX.finditer(row)))!=1:break
                    if parent.find(['h1','h2','h3','h4']):break
                    if SERVICE.search(row):
                        value=row;break
            records.extend(extract_text(value, url, "html block", context=section_context(el)))
    return list({observation_key(r): r for r in (qualify(item) for item in records)}.values())
