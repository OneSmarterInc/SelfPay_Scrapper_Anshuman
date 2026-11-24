# Clearprice — Self-Pay Hospital & Imaging Price Scraper

A local web app for researching published US hospital and imaging prices, contact details, and locations. Scan official websites, inspect the source route, compare old/new values, approve updates, and export readable Excel files.

The application, database, browser rendering, and PDF OCR run on your computer. Internet access is required to download dependencies and scan websites. No hosted AI service, paid API key, or cloud database is required.

## Requirements

- Windows 10/11, 64-bit. The Windows setup and launcher are provided and tested; macOS/Linux setup is not validated.
- Python **3.12 or newer, 64-bit**. **Python 3.14 is the tested version** for the pinned dependencies. Install from [python.org](https://www.python.org/downloads/windows/) and enable **Add Python to PATH**.
- Internet access and enough disk space for Python packages, Chromium, OCR models, and growing scan data.
- 16 GB RAM is recommended for the default four-center workflow. Browser rendering and OCR use more resources than ordinary HTML pages.
- Git is optional if you download the repository as a ZIP. Node.js is only needed to run the frontend developer tests.

## 1. Download the code

Using Git in PowerShell:

```powershell
git clone https://github.com/OneSmarterInc/SelfPay_Scrapper_Anshuman.git
cd SelfPay_Scrapper_Anshuman
```

Alternatively, choose **Code → Download ZIP** on GitHub, extract it, and open the extracted project folder. You can place the folder on any local drive; no specific user name or drive letter is required.

## 2. Install once

Double-click **setup.bat**, or run this from the project folder:

```powershell
.\setup.bat
```

Setup creates `.venv`, installs `requirements-lock.txt`, downloads Chromium into `data/browsers`, and initializes the local OCR engine/models. The first setup can take several minutes. Wait for **Setup complete**.

The installer prefers `py -3.14` when available, otherwise it uses `python` from PATH. If an old `.venv` already exists, use a fresh project folder with the supported Python version rather than mixing environments.

## 3. Start the portal

Double-click **run.bat**, or run:

```powershell
.\run.bat
```

Open **http://127.0.0.1:8765/**. The landing page is Dashboard. Keep the server terminal open and the computer awake while scanning. Closing a browser tab does not stop the server, but closing the server terminal does.

If another copy is already serving port 8765, `run.bat` opens that running copy. To use a different checkout, stop the previous server first.

## 4. Add your centers

**No original directory, local database, scan results, or exports are included in this repository.** A fresh installation starts empty. Choose one of these options:

### Enter website URLs

Open **New scan** and enter official public website URLs in the URL input. You can scan websites without importing a directory first. A hospital's pricing or location URL can be used as a starting point.

### Upload Excel files

On **New scan**, download the Excel template, fill it in, and upload one or more `.xlsx` files. Keep its headers and worksheet structure unchanged:

| Column | What to enter |
|---|---|
| Provider name | Hospital or imaging-center name |
| Official website | Complete `https://` or `http://` official URL |
| Directory record ID | Optional existing directory ID; leave blank for new providers |

Invalid headers, formulas, duplicate providers, unsupported URLs, and unknown supplied directory IDs produce validation errors. Use the app-generated template rather than renaming an arbitrary workbook.

Limits: 10 Excel files, 2 MB per file, and 20 providers total when files or typed URLs are included. Directory-only scans support up to 20,000 selected records.

### Import a directory CSV

Open **Directory → Import directory**. Use your compatible directory export or fill in [examples/directory-template.csv](examples/directory-template.csv).

The CSV needs the exact headers `Center or operator` and `Stable source record ID`. Put complete official URLs in `Current official evidence` or `Official pricing / program source`; a generic `Website` column is not used for scan selection. Use a unique stable ID for each record and retain it in later imports. Keep header names unchanged and quote cells containing commas.

Imports add new IDs; they do not overwrite previously edited records. Existing files with title rows above the header are supported when the header occurs within the first 25 rows. Select the records you want, then start the scan from **New scan**.

## 5. Scan, review, and export

1. **New scan:** choose your inputs and start. Choose **Centers at a time** (default **4**, positive whole numbers with no fixed application maximum). Centers run concurrently, with **1 center per website**, **2 browser renderers**, and **1 PDF processor** at a time.
2. **Running scans:** see each active center and URL, elapsed active time, counts, and **Pause / Resume / Stop**. Use **Centers at a time → Update** on Running scans to change concurrency. Increases start more available centers; decreases let active centers finish before reducing new starts. Each center has its own saved checkpoint. Pause waits for a safe point; an unfinished page may be retried on resume. Stop ends the scan; use Pause when you intend to continue.
3. **Scan history / View findings:** inspect price candidates, page coverage, failures, and source routes. A processed center is not a guarantee that prices were published or found.
4. **Review updates:** after a scan finishes, inspect paired **Old / New** values. Equivalent values are grouped; conflicts and uncertain findings need review. Use **Source & route** to verify the facility, exam, contrast, date, and included fees.
5. **Approve, then Apply:** approval alone does not change the directory. Apply saves eligible approved updates. Replacing a populated conflicting field requires the explicit replacement action. Reviewing raw evidence alone also does not apply directory changes.
6. **Directory → Download Excel:** exports the applied current values with old/new change details highlighted in yellow. Full CSV retains all fields and provenance. **Change history → Undo** reverses applied changes, newest first.

A center can retain multiple phone numbers and emails. **Approve addition**, then Apply, adds a contact without replacing existing values. Equivalent numbers (including country-code formatting) and case-insensitive emails are deduplicated; different phone extensions stay separate. Verify the department and facility using Source & route.

Review proposals are normally prepared when the batch ends; raw findings and coverage are available while it runs. Bulk selection follows the current filters across pages. Bulk approval excludes conflicts and possible duplicate locations; Apply handles up to 200 approved updates per action.

Price extraction excludes explicit competitor/market comparisons and standalone deposits or installments. Ambiguous multi-amount blocks remain subject to source review; these filters cannot guarantee every published figure is a valid exam price.

## How scanning stops

Pricing-related routes run first, followed by contact/location, about, relevant service pages, and other routes. Discovered pricing routes continue after the first usable price is found.

Once pricing routes have been checked, a center can stop when a usable self-pay price, phone, and address are found. Email is optional. If pricing is found but contacts are missing, the scanner checks up to 10 non-pricing contact/about pages, or stops sooner when those routes are exhausted. Without usable pricing, it continues through discoverable routes.

New scans have no fixed page-count or link-depth cap. Large websites can therefore take a long time. Centers sharing a website wait for that website's worker slot. There is no guaranteed completion time or universal accuracy percentage.

## Supported sources and limits

- Static HTML, local Chromium rendering for JavaScript content, text PDFs, scanned/image-bearing PDFs using local OCR, and supported CMS discounted-cash CSV/JSON formats.
- Downloads up to **100 MB** per file. Larger files and archives are not supported.
- Browser resource limits: 20 MB per resource, 100 MB total, and 200 requests per rendered page. Failures are recorded as partial coverage.
- Published price pages are fetched twice for consistency. This does not prove the amount applies to a particular facility or includes all fees.
- OCR prices are **review-only**: verify digits and exam labels against the PDF page. Re-downloading the same PDF does not validate OCR accuracy.
- Explicit sibling-branch location pages cannot satisfy the selected center's completion checks or generate applicable updates for that center.
- Robots restrictions, access denials, timeouts, and inaccessible pages remain visible. Missing evidence means unknown, not free or unavailable care.
- Rendering does not log in, solve CAPTCHAs, submit forms, or operate interactive pricing calculators. External pricing hosts require a separate scan. Reliable matching across external hosts remains a limitation.

## Saved data, backup, and recovery

Everything is saved under the project folder:

| Path | Contents |
|---|---|
| `data/enrichment.sqlite3` | Working directory, review proposals, and change history |
| `data/directory.json` | Original imported directory snapshot, when present |
| `data/<scan-id>.json` | Scan findings and worker checkpoints |
| `data/browsers/` | Downloaded Chromium runtime |
| `data/monitoring/<scan-id>/` | Optional monitor progress, source errors, and completion report |

These paths, environments, credentials, and exports are ignored by Git. Back up the **whole data folder** with the server stopped so the database and scan snapshots stay consistent. Preserve the old checkout and its data before upgrading.

After an unexpected server stop, start the portal again and use **Running scans → Resume**. Checkpointed scans become paused on startup. Older scans without checkpoints need a new scan. Updating code does not retroactively re-extract old findings.

### Optional local monitor

In a second PowerShell window, run:

```powershell
.\.venv\Scripts\python.exe scan_monitor.py YOUR_SCAN_ID
```

Replace `YOUR_SCAN_ID` with the 32-character ID at the end of the **View findings** URL (`#/scans/<id>`). The monitor targets the default local port, 8765. It logs progress, records source issues, respects manual pause/stop, and makes up to three process/checkpoint recovery attempts. It writes `completion.txt` when the monitored run ends. It cannot repair unknown software bugs or keep a sleeping computer scanning. Keep its terminal open; do not start duplicate monitors for the same scan.

## Troubleshooting

- **Python not found / version error:** install 64-bit Python 3.12+ (3.14 tested), enable PATH, reopen the terminal, and rerun setup in a fresh checkout.
- **Chromium missing:** rerun `setup.bat` from this project folder. It installs the browser into the location used by the scraper.
- **OCR/DLL import failure:** use 64-bit Python and install the Microsoft Visual C++ Redistributable for x64 if the dependency error requests it, then rerun setup.
- **403, CAPTCHA, robots restriction, or inaccessible pricing tool:** check the coverage entry. The scraper does not bypass these restrictions; review the official source manually where appropriate.
- **Pause takes time:** current requests or PDF pages need to reach a checkpoint. Do not force-close the process unless it is actually stuck.
- **No new value:** inspect scan coverage and the source route. Data may be unavailable, unchanged, inaccessible, or too ambiguous to propose safely.
- **Portal does not open:** wait for the server's startup message, then open http://127.0.0.1:8765/ manually. Check whether another copy is using that port.

## Development checks

After setup, from the project folder:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check static/app.js
node --test tests/*.test.cjs
```

The tests use synthetic fixtures and temporary data stores. Chromium and OCR must be installed for the extended-source tests. The OCR fixture uses the Windows Arial font. Node.js is only required for the JavaScript checks.

`requirements-lock.txt` records the tested dependency versions. `requirements.txt` lists direct dependency ranges. The runtime modules, local static UI, and regression tests are included; operational data and live benchmark snapshots are intentionally excluded.
