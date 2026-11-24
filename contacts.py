"""Published contact/location extraction; never invent personal or facility associations."""
import hashlib
import json
import re
from urllib.parse import unquote, urljoin, urlsplit
from bs4 import BeautifulSoup

FIELDS = {'address':'Current supported address', 'phone':'Current supported call number', 'email':'Current usable email'}
EMAIL = re.compile(r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}', re.I)
PHONE = re.compile(r'(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?:\s*(?:ext\.?|x)\s*\d{1,6})?', re.I)
MEDICAL = re.compile(r'hospital|imaging|radiology|medical|diagnostic|MRI|clinic', re.I)


def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def walk(value):
    if isinstance(value, list):
        for item in value: yield from walk(item)
    elif isinstance(value, dict):
        yield value
        for key in ('@graph', 'department', 'location', 'subOrganization', 'itemListElement', 'item'):
            if key in value: yield from walk(value[key])


def extract_contacts(raw, url):
    soup = BeautifulSoup(raw, 'html.parser')
    contacts, entities = [], []
    def add(kind, value, evidence, entity='', scope='Page-level contact; facility applicability requires review'):
        value=clean(value)
        if not value: return
        if kind=='email':
            found=EMAIL.fullmatch(value.removeprefix('mailto:'))
            if not found: return
            value=found.group().lower()
        if kind=='phone':
            digits=re.sub(r'\D','',value.split('ext')[0])
            if len(digits) not in (10,11) or (len(digits)==11 and not digits.startswith('1')): return
        contacts.append({'kind':kind,'field':FIELDS[kind],'value':value,'evidence':clean(evidence)[:2200], 'source_url':url,'entity':entity,'scope':scope})
    for script in soup.select('script[type="application/ld+json"]'):
        try: document=json.loads(script.string or script.get_text())
        except (ValueError, TypeError): continue
        for obj in walk(document):
            types=obj.get('@type',[])
            types=[types] if isinstance(types,str) else types
            if not isinstance(types,list) or not any(t in ('Hospital','MedicalClinic','DiagnosticLab','MedicalBusiness','LocalBusiness','Organization') for t in types): continue
            name=clean(obj.get('name',''))
            if not name: continue
            address=obj.get('address',{})
            if isinstance(address,list): address=address[0] if len(address)==1 else {}
            if isinstance(address,dict):
                street=clean(address.get('streetAddress',''))
                full=', '.join(clean(address.get(k,'')) for k in ('streetAddress','addressLocality','addressRegion','postalCode') if address.get(k))
                city=clean(address.get('addressLocality',''));state=clean(address.get('addressRegion',''))
            else: street=full=clean(address);city=state=''
            evidence=json.dumps({k:obj.get(k) for k in ('@type','name','address','telephone','email','url') if obj.get(k)},ensure_ascii=False)
            scope='Structured data for '+name+'; confirm it matches the selected facility'
            if street: add('address',full,evidence,name,scope)
            for kind,key in [('phone','telephone'),('email','email')]:
                values=obj.get(key,[]);values=values if isinstance(values,list) else [values]
                for value in values:
                    if isinstance(value,str): add(kind,value,evidence,name,scope)
            if MEDICAL.search(name+' '+' '.join(str(t) for t in types)) and street:
                entities.append({'name':name,'address':full,'city':city,'state':state,'url':url,'evidence':evidence})
    for tag in soup.select('script,style,template,noscript,[hidden],[aria-hidden=true]'): tag.decompose()
    def full_address(value):
        return len(value)<350 and re.match(r'^\d{1,6}\s',value) and re.search(r'\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b',value)
    for row in soup.select('tr'):
        cells=row.find_all(['td','th'],recursive=False)
        if len(cells)==2 and re.fullmatch(r'(?:street |mailing |office )?address[: ]*',cells[0].get_text(' ',strip=True),re.I):
            value=clean(cells[1].get_text(' ',strip=True))
            if full_address(value):add('address',value,row.get_text(' ',strip=True),scope='Published address row; verify branch applicability')
    map_labels={}
    for anchor in soup.select('a[href]'):
        href=anchor['href']
        if (urlsplit(href).hostname or '').lower()=='maps.app.goo.gl':
            label=clean(anchor.get_text(' ',strip=True))
            if label:map_labels.setdefault(href,[]).append(label)
    for labels in map_labels.values():
        labels=list(dict.fromkeys(labels))
        streets=[label for label in labels if re.match(r'^\d{1,6}\s',label)]
        localities=[label for label in labels if re.search(r'\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b',label) and label not in streets]
        if len(labels)==2 and len(streets)==len(localities)==1:
            value=streets[0].rstrip(',')+', '+localities[0]
            if full_address(value):add('address',value,value,scope='Published address parts share the same map destination; verify branch applicability')
    for anchor in soup.select('a[href]'):
        href=unquote(anchor['href'])
        parent=anchor.find_parent(['p','li','address','div'])
        evidence=(parent or anchor).get_text(' ',strip=True)[:2200]
        label=anchor.get_text(' ',strip=True)
        host=(urlsplit(href).hostname or '').lower()
        if host in ('maps.app.goo.gl','maps.google.com','www.google.com','google.com') and re.match(r'^\d{1,6}\s',label) and re.search(r'\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b',label):
            add('address',label,label,scope='Published map-link address; verify branch applicability')
        if href.lower().startswith('mailto:'):
            for match in EMAIL.findall(href.split('?',1)[0]): add('email',match,evidence)
        if href.lower().startswith('tel:') and not re.search(r'fax',str(parent or anchor),re.I):
            add('phone',href[4:].split('?',1)[0],evidence)
    for address in soup.select('address'):
        text=address.get_text(' ',strip=True)
        if re.search(r'\b\d{5}(?:-\d{4})?\b',text) and re.search(r'\b\d+\s+\w+',text): add('address',text,text)
    # Visible text only; don't mine script bundles for unrelated developer contacts.
    for node in soup.find_all(string=True):
        line=clean(node)
        if len(line)>1600: continue
        for email in EMAIL.findall(line): add('email',email,line)
        block=node.find_parent(['p','li','address','td','div'])
        # Fax labels/icons can be in a separate sibling from the number.
        context=str(block or node)
        if not re.search(r'fax',context,re.I):
            for phone in PHONE.findall(line): add('phone',phone,(block.get_text(' ',strip=True) if block else line))
    unique={}
    for item in contacts:
        key=(item['kind'],item['value'],item['entity'])
        unique.setdefault(key,item)
    return list(unique.values()),list({(e['name'],e['address']):e for e in entities}.values())
