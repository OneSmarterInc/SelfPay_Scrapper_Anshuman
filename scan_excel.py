"""Strict local scan-workbook contract; no macros, formulas or external resources."""
import base64
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET
from urllib.parse import urlsplit
from crawler import canonical
from excel_export import workbook

HEADERS = ['Provider name', 'Official website', 'Directory record ID (optional)']
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def template():
    return workbook([('Providers', HEADERS, [], [36, 65, 32], {}), ('Instructions', ['Rule', 'Details'], [
        ['Required sheet', 'Keep the Providers sheet and its three headers exactly as supplied. Add one provider per row starting at row 2.'],
        ['Required values', 'Provider name and a complete official http:// or https:// website are required.'],
        ['Existing records', 'Optionally copy a record ID from the Directory. The website host must match that record. Leave blank for a new website scan.'],
        ['Limits', 'Up to 10 .xlsx files, 2 MB each, and 20 total providers including selected directory records and typed URLs.'],
        ['Validation', 'No formulas, merged cells, extra columns, duplicate websites or duplicate record IDs. All files must pass before scanning.'],
        ['Privacy', 'Files are processed locally. Uploading does not replace the directory. Prices still require source review.']
    ], [24, 100], {})])

def xml(z, path):
    data = z.read(path)
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('XML entities are not supported.')
    return ET.fromstring(data)

def read_file(name, encoded):
    if not name.lower().endswith('.xlsx'):
        raise ValueError('Only .xlsx files are accepted; download the template.')
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('File exceeds 2 MB.')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if len(z.infolist()) > 150 or sum(i.file_size for i in z.infolist()) > 12 * 1024 * 1024:
                raise ValueError('Workbook exceeds the supported size.')
            if any('vbaproject' in n.lower() or 'externallinks/' in n.lower() for n in z.namelist()):
                raise ValueError('Macros and external workbook links are not allowed.')
            book = xml(z, 'xl/workbook.xml')
            sheets = book.findall('s:sheets/s:sheet', NS)
            if [s.get('name') for s in sheets].count('Providers') != 1 or any(s.get('name') not in ('Providers', 'Instructions') for s in sheets):
                raise ValueError('Use the Providers sheet; only the optional Instructions sheet is allowed.')
            sheet = next(s for s in sheets if s.get('name') == 'Providers')
            rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            rel = next(r for r in xml(z, 'xl/_rels/workbook.xml.rels') if r.get('Id') == rid)
            path = posixpath.normpath(posixpath.join('xl', rel.get('Target', ''))) if not rel.get('Target', '').startswith('/') else rel.get('Target').lstrip('/')
            if rel.get('TargetMode') == 'External' or not path.startswith('xl/worksheets/'):
                raise ValueError('Invalid Providers sheet relationship.')
            root = xml(z, path)
            if root.find('s:mergeCells', NS) is not None:
                raise ValueError('Providers: merged cells are not allowed.')
            strings = [''.join(n.itertext()) for n in xml(z, 'xl/sharedStrings.xml').findall('s:si', NS)] if 'xl/sharedStrings.xml' in z.namelist() else []
            rows = []
            seen_rows = set()
            for row in root.findall('s:sheetData/s:row', NS):
                number = int(row.get('r'))
                if number < 1 or number in seen_rows:
                    raise ValueError(f'Providers row {number}: invalid or duplicate row number.')
                seen_rows.add(number)
                values = {}
                seen_cells = set()
                for cell in row.findall('s:c', NS):
                    if cell.find('s:f', NS) is not None:
                        raise ValueError(f'Providers row {number}: formulas are not allowed.')
                    address = cell.get('r', '')
                    if not re.fullmatch(r'[A-Z]+'+str(number), address) or address in seen_cells:
                        raise ValueError(f'Providers row {number}: invalid or duplicate cell reference.')
                    seen_cells.add(address)
                    kind = cell.get('t')
                    if kind in ('e', 'b'):
                        raise ValueError(f'Providers row {number}: enter text, not Excel errors or Boolean values.')
                    value = ''.join(cell.find('s:is', NS).itertext()) if kind == 'inlineStr' else cell.findtext('s:v', '', NS)
                    if kind == 's': value = strings[int(value)]
                    col = re.sub(r'\d', '', cell.get('r', ''))
                    if value.strip(): values[col] = value.strip()
                if any(c not in ('A', 'B', 'C') for c in values):
                    raise ValueError(f'Providers row {number}: unexpected extra column.')
                if number == 1:
                    if [values.get(c, '') for c in 'ABC'] != HEADERS:
                        raise ValueError('Providers row 1: headers must exactly match the downloaded template.')
                elif values:
                    if not values.get('A') or not values.get('B'):
                        raise ValueError(f'Providers row {number}: Provider name and Official website are required.')
                    try:
                        url = canonical(values['B'])
                    except ValueError as exc:
                        raise ValueError(f'Providers row {number}, Official website: {exc}') from exc
                    if len(values['A']) > 200:
                        raise ValueError(f'Providers row {number}: Provider name is too long.')
                    rows.append({'name': values['A'], 'url': url, 'record_id': values.get('C') or None, 'upload_file': name, 'upload_row': number})
            first = root.find('s:sheetData/s:row', NS)
            if first is None or first.get('r') != '1': raise ValueError('Providers row 1: template headers are missing.')
            if not rows: raise ValueError('Providers: add at least one provider below the headers.')
            if len(rows) > 20: raise ValueError('Providers: maximum 20 providers per scan.')
            return rows
    except (zipfile.BadZipFile, KeyError, ET.ParseError, IndexError, StopIteration, TypeError, AttributeError, RuntimeError, NotImplementedError) as exc:
        raise ValueError('Invalid or damaged Excel workbook. Use the downloaded template.') from exc

def validate_files(files, indexed):
    rows, errors = [], []
    for file in files:
        try: rows.extend(read_file(file.name, file.content))
        except ValueError as exc: errors.append(f'{file.name}: {exc}')
    seen_urls, seen_ids = set(), set()
    for row in rows:
        label = f"{row['upload_file']} / Providers row {row['upload_row']}"
        rid = row['record_id']
        if row['url'] in seen_urls or (rid and rid in seen_ids): errors.append(label + ': duplicate website or directory record ID across the selected files.')
        seen_urls.add(row['url']); seen_ids.add(rid)
        if rid:
            record = indexed.get(rid)
            if not record: errors.append(label + ': directory record ID does not exist.')
            elif urlsplit(row['url']).hostname.removeprefix('www.') not in {urlsplit(u).hostname.removeprefix('www.') for u in record['urls'] if urlsplit(u).hostname}:
                errors.append(label + ': website host does not match the selected directory record.')
    if errors: raise ValueError('\n'.join(errors))
    return rows
