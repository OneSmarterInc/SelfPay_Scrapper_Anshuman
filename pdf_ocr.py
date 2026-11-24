"""Offline OCR for image-based PDF pages; evidence always needs visual review."""
import threading
_engine=None
_lock=threading.Lock()


def engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR
        _engine=RapidOCR()
    return _engine


def ocr_page(raw,number,url):
    import pypdfium2 as pdfium
    from extraction import extract_text
    with _lock:
        document=pdfium.PdfDocument(raw)
        try:
            page=document[number-1]
            scale=min(3,2400/max(page.get_width(),page.get_height()))
            bitmap=page.render(scale=scale)
            try:result=engine()(bitmap.to_pil())
            finally:bitmap.close();page.close()
        finally:document.close()
    if result.boxes is None:return [],0
    blocks=sorted(zip(result.boxes,result.txts,result.scores),key=lambda item:(min(p[1] for p in item[0]),min(p[0] for p in item[0])))
    lines=[]
    for box,text,score in blocks:
        y=sum(p[1] for p in box)/4;height=max(p[1] for p in box)-min(p[1] for p in box)
        target=next((line for line in reversed(lines[-3:]) if abs(line['y']-y)<max(8,height*.45)),None)
        if target is None:target={'y':y,'parts':[]};lines.append(target)
        target['parts'].append((min(p[0] for p in box),text,float(score)))
    context=' '.join(text for _,text,_ in blocks)[:1000]
    rows=[];characters=0
    for line in lines:
        parts=sorted(line['parts']);text=' '.join(p[1] for p in parts);characters+=len(text)
        for row in extract_text(text,url,'pdf OCR',page=number,context=context):
            row['ocr_confidence']=min(p[2] for p in parts)
            row['extraction_issues'].append('OCR reading: verify the amount and exam against the PDF page')
            row['confidence']='needs review';rows.append(row)
    return rows,characters
