"""Explainable location candidates. Never merge records or infer branches from a domain alone."""
import re
import unicodedata
from urllib.parse import urlsplit
from contact_values import MULTI_FIELDS, values, identity

def words(value):
    return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode().lower()).strip()

def address_parts(value):
    value=str(value or '').lower()
    unit=re.search(r'(?:\b(?:suite|ste|unit|apt)\.?\s*|#\s*)([a-z0-9-]+)',value)
    unit=unit.group(1) if unit else ''
    base=re.sub(r'(?:\b(?:suite|ste|unit|apt)\.?\s*|#\s*)[a-z0-9-]+',' ',value)
    base=words(base)
    for long,short in [('street','st'),('road','rd'),('avenue','ave'),('boulevard','blvd'),('drive','dr'),('lane','ln'),('parkway','pkwy'),('highway','hwy'),('north','n'),('south','s'),('east','e'),('west','w')]:
        base=re.sub(r'\b'+long+r'\b',short,base)
    return base,unit

def name_key(name):
    tokens=words(name).split()
    return ' '.join(t for t in tokens if t not in ('llc','inc','incorporated','corp','corporation','the'))

def hosts(urls):
    return {(urlsplit(u).hostname or '').removeprefix('www.') for u in urls if u}

class FacilityIndex:
    def __init__(self,records):
        self.rows=[]
        for row in records:
            source=row.get('source',{})
            address=source.get('Current supported address','')
            base,unit=address_parts(address)
            self.rows.append((row,base,unit,name_key(row.get('name')),hosts(row.get('urls',[]))))
    def candidates(self,entity,exclude_id=None):
        base,unit=address_parts(entity.get('address',''))
        name=name_key(entity.get('name')); domain=hosts([entity.get('url','')])
        result=[]
        for row,other_base,other_unit,other_name,other_hosts in self.rows:
            if row['id']==exclude_id:continue
            # Explicitly different suites are separate locations, even for similar names.
            if base and base==other_base and unit and other_unit and unit!=other_unit:continue
            reason=''
            if base and base==other_base:
                reason='Same normalized street address'+('; suite is missing on one source' if unit!=other_unit else '')+'. Check for aliases or co-located providers.'
            elif name and name==other_name and (not base or not other_base):
                city=words(entity.get('city'));other_city=words(row.get('city'))
                state=words(entity.get('state'));other_state=words(row.get('state'))
                if city and other_city and city!=other_city or state and other_state and state!=other_state:continue
                if (city and city==other_city) or domain & other_hosts:
                    reason='Same normalized provider name and matching city or website; street address is incomplete.'
            if reason:result.append({'id':row['id'],'name':row['name'],'address':row.get('source',{}).get('Current supported address',''),'reason':reason})
        return result


def field_key(field,value):
    text=str(value or '').strip()
    if field in MULTI_FIELDS:
        found=values(field,text)
        if found:return tuple(sorted(identity(field,item) for item in found))
    if field=='Current supported call number':
        parts=re.split(r'\s*(?:ext\.?|extension|x)\s*',text,maxsplit=1,flags=re.I)
        digits=re.sub(r'\D','',parts[0])
        if len(digits)==11 and digits.startswith('1'):digits=digits[1:]
        return (digits,re.sub(r'\D','',parts[1]) if len(parts)>1 else '')
    if field=='Current supported address':return address_parts(text)
    return re.sub(r'\s+',' ',text).casefold()


def location_scope_issue(row, url):
    if not row or re.search(r'operator|network|statewide|multiple|regional',row.get('scope',''),re.I):return ''
    city=words(row.get('city',''))
    if not city:return ''
    target=urlsplit(url or '')
    def location(path):
        match=re.search(r'/(?:our-)?locations?/(.+)',path.rstrip('/'),re.I)
        return match.group(1).split('/') if match else []
    target_parts=location(target.path)
    if not target_parts:return ''
    references=[]
    for source in row.get('urls',[]):
        parsed=urlsplit(source)
        if (parsed.hostname or '').removeprefix('www.')!=(target.hostname or '').removeprefix('www.'):continue
        parts=location(parsed.path)
        if parts and words(parts[-1])==city:references.append(parts)
    if not references:return ''
    if any(target_parts[:len(parts)]==parts or parts[:len(target_parts)]==target_parts for parts in references):return ''
    return 'Different location page; verify facility applicability'
