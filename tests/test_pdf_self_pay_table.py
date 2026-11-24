from pdf_prices import extract_pdf_page
from imaging import enrich

class Page:
    def __init__(self,layout):self.layout=layout
    def extract_text(self,**kwargs):return self.layout

def test_self_pay_column_and_footnotes():
    page=Page("PROCEDURE                           SELF-PAY\n                              AT TIME OF SERVICE\nMRI without Contrast                    $900\nCTA Head and Neck                       $1,950\nUltrasound                              $600*\n* this procedure involves a separate lab bill.\n")
    rows,_=extract_pdf_page(page,'https://example.com/prices.pdf',1)
    rows=[enrich(r) for r in rows]
    assert [r['amount'] for r in rows]==['900','1950','600']
    assert all(r['price_type']=='self-pay / cash' for r in rows)
    assert rows[1]['modality']=='CT'
    assert 'separate lab bill' in rows[2]['context']
    assert 'separate lab bill' not in rows[0]['context']

def test_mixed_payer_columns_do_not_inherit_self_pay():
    page=Page("PROCEDURE             SELF-PAY      INSURANCE\nMRI without contrast     $900          $2000\n")
    rows,_=extract_pdf_page(page,'https://example.com/prices.pdf',1)
    assert all(enrich(r)['price_type']!='self-pay / cash' for r in rows)

def test_prose_self_pay_mention_does_not_label_unrelated_amounts():
    page=Page("Ask about self-pay discounts.\nMRI without contrast $2000\n")
    rows,_=extract_pdf_page(page,'https://example.com/prices.pdf',1)
    assert enrich(rows[0])['price_type']!='self-pay / cash'
