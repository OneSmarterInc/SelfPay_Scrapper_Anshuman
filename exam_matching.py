"""Conservative comparisons of explicit exam details; no inferred medical code mappings."""
import re
from decimal import Decimal

def exam_scope(text, modality='', cpt='', context=''):
    text=str(text or '').lower()
    text=re.sub(r'\bw/o\s*', 'without ', text)
    text=re.sub(r'\bw/\s*', 'with ', text)
    modalities=[name for name,pattern in [('MRI',r'\bmri\b|magnetic resonance'),('CT',r'\bct\b|computed tomography'),('PET',r'\bpet\b'),('Ultrasound',r'ultrasound|sonograph'),('X-ray',r'x[ -]?ray'),('Mammography',r'mammogra'),('DEXA',r'\bdexa\b|\bdxa\b')] if re.search(pattern,text)]
    body=[name for name in ['brain','head','neck','cervical','thoracic','lumbar','chest','abdomen','pelvis','breast','shoulder','elbow','wrist','hand','hip','knee','ankle','foot'] if re.search(r'\b'+name+r'\b',text)]
    contrast=''
    if re.search(r'(?:with(?:out)?\s+(?:and|&)\s+with(?:out)?)\s+contrast',text):contrast='with and without'
    elif re.search(r'without contrast|non[- ]?contrast',text):contrast='without'
    elif re.search(r'with contrast',text):contrast='with'
    codes=sorted(set(re.findall(r'\b(?:\d{5}|[A-Z]\d{4})\b',str(cpt).upper()))) if cpt else sorted(set(re.findall(r'(?:cpt|hcpcs|code)\s*[:#]?\s*(\d{5}|[a-z]\d{4})',text)))
    codes=[code.upper() for code in codes]
    fee_text=text+' '+str(context or '').lower()
    fees=''
    if re.search(r'(?:exclud\w*|not includ\w*|additional|separate)\s+(?:the\s+)?(?:radiologist|professional|interpretation|reading)',fee_text):fees='professional fee excluded'
    elif re.search(r'not all[- ]inclusive',fee_text):fees='not all-inclusive'
    elif re.search(r'all[- ]inclusive|includes?\s+(?:the\s+)?(?:radiologist|professional|interpretation|reading)',fee_text):fees='professional fee included'
    elif re.search(r'\btechnical (?:only|component)\b',fee_text):fees='technical component'
    elif re.search(r'\bprofessional (?:only|component)\b',fee_text):fees='professional component'
    qualifier='starting at' if re.search(r'start(?:ing|s)? (?:at|from)|\bfrom\s*\$',text) else 'range' if re.search(r'\$[\d,.]+\s*[-–]\s*\$?[\d,.]+',text) else 'listed'
    return {'modality':', '.join(modalities) or modality,'body_part':', '.join(body),'contrast':contrast,'cpt':', '.join(codes),'laterality':', '.join(k for k in ['left','right','bilateral'] if re.search(r'\b'+k+r'\b',text)), 'fees':fees,'qualifier':qualifier}

def amounts(text):
    found=re.findall(r'(?:\$|USD\s*)([\d,]+(?:\.\d+)?)',str(text),re.I)
    if not found and re.fullmatch(r'\s*\d+(?:\.\d+)?\s*',str(text)):found=[str(text).strip()]
    return {Decimal(n.replace(',','')) for n in found}

def compare_exam(old, new, new_scope=None, modality=''):
    # Never pool multiple legacy offers into one synthetic exam.
    old_scope=exam_scope(old,modality=modality)
    new_scope=new_scope or exam_scope(new)
    mismatches=[]; unknown=[]
    for key in ['modality','body_part','contrast','cpt','laterality','fees','qualifier']:
        a,b=old_scope.get(key,''),new_scope.get(key,'')
        if a and b and a!=b:mismatches.append(key.replace('_',' '))
        elif key in ('modality','body_part','contrast','fees') and (not a or not b):unknown.append(key.replace('_',' '))
        elif key in ('cpt','laterality') and bool(a)!=bool(b):unknown.append(key)
    if any(',' in scope.get(key,'') for scope in (old_scope,new_scope) for key in ('modality','cpt')):
        unknown.append('multiple modalities or codes')
    old_amounts,new_amounts=amounts(old),amounts(new)
    if len(old_amounts)!=1 or len(new_amounts)!=1 or ';' in str(old) or '\n' in str(old) or len(re.findall(r'(?:\$|USD\s*)[\d,]+',str(old),re.I))>1:unknown.append('single exam / amount association')
    ambiguous='single exam / amount association' in unknown or 'multiple modalities or codes' in unknown
    state='unknown' if ambiguous else 'different_scope' if mismatches else 'unknown' if unknown else 'comparable'
    reason='Different '+', '.join(mismatches)+'. Do not treat these as the same offer.' if state=='different_scope' else 'Unresolved '+', '.join(unknown)+'. Verify the original exam and fee details.' if unknown else 'Explicit exam details agree. Facility applicability and final fees still require source review.'
    return {'state':state,'reason':reason,'previous':old_scope,'found':new_scope,'amount_changed':state=='comparable' and old_amounts!=new_amounts}
