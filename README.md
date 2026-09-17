# Khwaja Dairy — Ledger Reconciliation

**Turn two ledger exports into an understandable reconciliation dashboard and a detailed Excel report.**

A Python and Streamlit application for comparing transaction records, investigating differences, and tracking review decisions. It accepts CSV, Excel and supported PDF ledger layouts, groups related transactions, suggests counterparts, and presents the results in plain language.

Built for a practical workflow: **upload → reconcile → understand → review → export**.

> **Deployment status:** suitable for local evaluation and demonstrations with synthetic data. The current app has shared SQLite history and no built-in user authentication or client-level access controls. Do not expose confidential ledgers through a public deployment.

## Highlights

- **Multiple input formats:** CSV, Excel (`.xlsx`, `.xls`, with the appropriate reader installed), and supported text/scanned PDFs.
- **Consistent transaction schema:** extraction paths converge on date, reference, description, amount and source filename.
- **Business-level grouping:** related goods, brokery/commission and freight entries can be grouped before comparison.
- **Candidate-based matching:** narrows likely counterparts before scoring, then makes deterministic one-to-one group assignments.
- **Readable dashboard:** ledger gap, match coverage, exact-value matches, unresolved differences, top causes and suggested next steps.
- **Visible progress:** extraction, grouping, matching and report-generation stages.
- **Manual exception review:** save OPEN, UNDER REVIEW, RESOLVED or WAIVED decisions with reviewer names and notes.
- **Saved history:** reopen earlier runs without repeating extraction while the database remains available.
- **Detailed Excel export:** seven sheets with source headings, reconciliation references, differences and suggested actions.

This is a rule-based reconciliation assistant—not an LLM service, an accounting system, or an automated settlement tool. No Power BI subscription or external AI API key is required for the current workflow.

## Quick start

Open a terminal in the project folder containing `app.py` and `requirements.txt`.

### Windows / PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

These commands use the virtual environment directly, without changing PowerShell execution policies. If `.venv` already exists and works, reuse it rather than recreating it.

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

Open the local URL printed by Streamlit, normally `http://localhost:8501`.

Use the same Python version for local testing and deployment. The dashboard tests were run on Python 3.12 with Streamlit 1.64.0; that is not a guarantee that every OCR dependency works with every Python version. The updated UI expects Streamlit 1.38 or newer.

### PDF and OCR prerequisites

Python packages and system programs are separate dependencies:

| Component | Purpose |
| --- | --- |
| `pdf2image` | Python interface for rendering PDF pages. |
| Poppler | Provides `pdfinfo` and PDF-rendering executables. |
| `pytesseract` | Python interface to the OCR engine. |
| Tesseract OCR | Reads text from rendered page images. |

Install the Python packages through `requirements.txt`. Install Poppler and Tesseract separately using trusted, approved distributions. See the [pdf2image installation guide](https://pdf2image.readthedocs.io/en/latest/installation.html) for platform-specific Poppler setup.

On Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr
```

On Windows, make the appropriate Poppler `bin` directory and Tesseract executable available to the app. After changing PATH, restart the terminal and Streamlit. Verify an approved installation with:

```powershell
pdfinfo -v
pdftoppm -v
tesseract --version
```

If Windows reports an Application Control block, stop and resolve the installation through the device's authorized administrator or software-approval process. Disabling security protection is not an application prerequisite.

## Using the app

1. Upload the first and second source ledgers.
2. Confirm that the files cover comparable periods and use an agreed sign convention.
3. Set the amount tolerance and click **Reconcile ledgers**.
4. Read the dashboard result and formula before interpreting individual differences.
5. Inspect the largest items, their source references and supporting evidence.
6. In **Manual Review**, choose a reference and save a decision, reviewer and notes.
7. Download the original detailed Excel report, or reopen the run later from **Saved reconciliations**.

Selecting new uploads does not replace a displayed report until another reconciliation completes. The displayed filenames identify the run currently being viewed.

## Understanding the dashboard

### The central calculation

```text
Required adjustment = Second ledger total − First ledger total
First ledger total + Required adjustment = Second ledger total
```

For example, using fictional values:

```text
First ledger:          PKR  500,000.00
Second ledger:         PKR -200,000.00
Required adjustment:   PKR -700,000.00

500,000 + (-700,000) = -200,000
```

The second ledger is PKR 700,000 lower. This does **not** establish who owes whom or authorize an accounting entry.

The balance cards are sums of **extracted signed transactions**, not independently verified closing balances. Missing opening balances, excluded rows or different date coverage can change their meaning.

### Key performance indicators

| KPI | What it means |
| --- | --- |
| Books differ by | Absolute size of the net gap, with a higher/lower explanation. |
| Required adjustment | Signed second-minus-first gap. |
| Group match rate | Paired groups divided by paired plus unpaired groups. A paired group may still have a value difference. |
| Exact-value matches | Paired amounts equal to the paisa. This does not certify dates or descriptions. |
| Accepted within tolerance | Non-zero paired differences the engine classified as MATCH. |
| Groups without a counterpart | Groups for which no counterpart was selected. |
| High-priority items still open | HIGH-priority exceptions marked OPEN or UNDER REVIEW. |
| Unresolved review items | Value exceptions still OPEN or UNDER REVIEW. |
| Unresolved difference value | Sum of absolute unresolved differences; review exposure, not confirmed loss or debt. |

**Net and gross are different:** an increase of PKR 100,000 and a decrease of PKR 100,000 produce a zero net gap, but PKR 200,000 of gross differences to investigate.

The dashboard also includes top-five category comparisons, priority and review-status charts, a largest-items table, suggested actions and an arithmetic proof. Table filters affect the largest-items table only; the headline KPIs and charts continue to describe the whole run.

### Health labels

| Status | Rule |
| --- | --- |
| Critical | Inconsistent report/source totals, or an unresolved HIGH-priority exception. |
| Needs Review | Other open exceptions, a non-zero gap, unpaired groups or non-value findings. |
| Good | None of those conditions remain; source completeness still requires verification. |
| No data | No source rows or transaction groups are available to assess. |

These are transparent workflow flags, not audit opinions or statistical confidence scores.

## Input data and reconciliation logic

### Structured files

CSV/Excel column discovery recognizes common aliases such as `Posting Date`, `Voucher`, `Memo`, `Debit` and `Credit`.

- A usable date and monetary field are required.
- If a recognized Amount column exists, it is used.
- Otherwise, debit/credit fields are converted to `amount = credit − debit`; a missing side is treated as zero.
- Reference and description are useful matching evidence, but can be blank.
- ISO dates are handled explicitly; other common date strings are interpreted day-first.
- Rows without a usable date or amount are excluded during normalization. Check extracted row counts against the original files.

Internal schema:

```text
date | reference | description | amount | source_file
```

### PDFs

The project includes layout-specific extraction for QuickBooks-style Account QuickReport and KD Feeds-style Business Partner Ledger documents, including OCR support for scanned pages. It is not a universal PDF-table reader: new layouts, damaged scans and merged OCR text may require parser changes.

### Matching workflow

1. Extract and normalize each ledger.
2. Identify available business attributes such as vehicle, invoice/purchase reference, product, weight, rate, bags and ancillary charges.
3. Group related rows into business transactions.
4. Retrieve candidates using vehicle, product/weight, date, amount and special-group indexes.
5. Score plausible pairs and select the highest-scoring available pairs first.
6. Retain unmatched groups and calculate signed differences.
7. Build report tables and save the run.

One-to-one assignment prevents reuse of a selected **group**. It does not guarantee that the selected pair is correct or that greedy assignment finds the globally optimal solution. Candidate indexing reduces comparisons, but performance still depends on ledger size, repeated values and OCR workload; no fixed speedup is promised.

Grouping is currently asymmetric and tailored to the original ledger layouts. The two upload positions are not guaranteed to be interchangeable. Confirm source ordering when onboarding another ledger format.

**Settings limitation:** the detailed engine uses internal date windows. The date-tolerance field is not currently applied uniformly to that engine, even though the row-level matcher supports it. Do not treat that field as a strict date cutoff for every detailed match.

## Excel report

| Worksheet | Contents |
| --- | --- |
| 1. Summary | Ledger totals, category summary, proof and executive findings. |
| 2. Difference Register | Value discrepancies and unmatched groups requiring investigation. |
| 3. Action List | Suggested priority, owner, action and evidence. |
| 4. Non-Value Differences | Detected control issues without direct value impact. |
| 5. Matched Comparison | Paired groups, amounts, dates, narrations and differences. |
| 6. Left Ledger | First source's extracted rows and reconciliation references. |
| 7. Right Ledger | Second source's extracted rows and reconciliation references. |

Source headings identify both files. Comparison columns use filename-derived labels such as `supplier_ledger_amount` instead of only generic left/right labels.

Categories and owners are suggestions requiring human confirmation. Review any settlement-direction wording in the workbook against the actual accounting convention; the dashboard's arithmetic alone does not validate it.

## Review decisions and saved history

The app creates a local SQLite database at:

```text
data/reconciliation_history.db
```

It stores reconciliation results, Excel report bytes and review decisions. To select another database file, set `RECONCILIATION_DB_PATH` before launching the app:

```powershell
$env:RECONCILIATION_DB_PATH = "D:\ReconciliationData\history.db"
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The directory must be writable. This setting changes the file location; it does not add PostgreSQL support or make storage durable on a hosted platform.

Important behaviour:

- OPEN and UNDER REVIEW count toward unresolved value.
- RESOLVED and WAIVED are kept separate and excluded from that unresolved total.
- Saving a decision refreshes dashboard counts, but does not rewrite ledger amounts or post accounting entries.
- The Excel download remains the original reconciliation; subsequent review notes are not written back into it.
- Manual review currently records exception decisions. It does not allow users to re-pair transactions or edit original amounts.
- History is shared within the app, not isolated per user or client.
- Review updates replace the current decision; an append-only change-history audit trail is not implemented.

Back up the database consistently and test recovery. Stop the app before copying database files, or use a SQLite-aware backup process. Treat the database as confidential: it contains ledger data.

## Deploy a demonstration on Streamlit Community Cloud

### 1. Prepare the repository

Upload the **complete working project**, with all updates applied—not just an incremental update ZIP. Put this README beside `app.py` at the repository root where practical.

Include the source files, `requirements.txt`, tests and the `packages.txt` described below. Exclude private data, credentials, virtual environments and generated databases.

Suggested additions to `.gitignore`:

```gitignore
.venv/
venv/
__pycache__/
*.py[cod]
.pytest_cache/
.env
.env.*
!.env.example
.streamlit/secrets.toml
data/
uploads/
reports/
*.db
*.db-wal
*.db-shm
*.sqlite
*.sqlite3
```

Keep synthetic examples in a separate reviewed directory if needed. Ignore rules do not remove already committed files; inspect the repository and commit history before publishing. Never upload real client ledgers, financial screenshots or saved report exports to a public repository.

### 2. Check Python and operating-system dependencies

Keep the project's complete `requirements.txt`; make sure it includes `streamlit`, `pandas`, `openpyxl`, `pdf2image`, `pytesseract`, `Pillow`, `rapidfuzz` and any additional dependencies imported by the existing extractors. Legacy `.xls` reading also needs its compatible reader. Test dependency versions in a clean environment rather than assuming packages installed on your laptop exist in the cloud.

Create or update **`packages.txt` at repository root** with:

```text
poppler-utils
tesseract-ocr
```

Community Cloud installs Python dependencies from the requirements file and Linux system packages from `packages.txt`. Your Windows Poppler folder is not available there. Remove any unconditional Windows-only executable paths before deploying. [Dependency documentation](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)

### 3. Create the app

1. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) and connect the relevant GitHub repository.
2. Choose **Create app** and select your repository, branch and `app.py` entry point.
3. If the project is nested, enter its actual relative path to `app.py`.
4. In advanced settings, choose the Python version used in your deployment tests.
5. Deploy and inspect build logs if startup fails.
6. Test CSV, Excel and PDF flows using synthetic data before sharing the URL.

See the [official deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy). Put future credentials in the platform's secrets settings, never in source code or README examples. [Secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

### 4. Understand hosting limits

Browser session state is not durable storage. Do not rely on a hosted local SQLite file for business-critical history without an explicit persistent-storage guarantee and a tested backup plan. Redeploy/restart recovery must be tested separately from an ordinary Streamlit rerun.

**A public demo should contain only synthetic data.** Everyone who can access this version may be able to inspect its shared history. A private GitHub repository does not add in-app client isolation.

For real client use, first add authentication, authorization on every read/write, client-scoped storage and backup/retention controls. PostgreSQL is a planned migration—not currently connected, and not enabled simply by setting `DATABASE_URL`.

## Main components

| Path | Responsibility |
| --- | --- |
| `app.py` | Uploads, progress, dashboard integration, review and history navigation. |
| `src/extraction/` | File readers, structured-column discovery, PDF/OCR parsing and normalization. |
| `src/reconciliation/matcher.py` | Row-level matching and shared settings/helpers. |
| `src/reconciliation/detailed_reconciliation.py` | Business grouping, candidate selection and detailed result tables. |
| `src/reconciliation/dashboard.py` | KPI calculations, review-state aggregation and health rules. |
| `src/reconciliation/dashboard_ui.py` | Dashboard cards, explanations, charts and filters. |
| `src/reconciliation/report.py` | Excel report formatting and export. |
| `src/reconciliation/history.py` | SQLite run storage and current review decisions. |
| `tests/test_reconciliation.py` | Core matching and extraction regression tests. |
| `tests/test_dashboard.py` | Dashboard calculations and Streamlit interaction tests. |

## Tests

From the project root, using the same environment as the app:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests/test_reconciliation.py tests/test_dashboard.py -q
```

The dashboard update passed 17 automated tests, covering signed/gross differences, exact versus tolerance matches, group-count denominators, empty inputs, proof inconsistencies, review states, filters, history loading and immediate KPI refresh.

The main-app UI test isolates file-extractor imports; it is not an end-to-end OCR test. Test representative PDFs and scan quality separately. No claim of universal ledger accuracy or accounting certification is made.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `No module named streamlit` | Install requirements into the same environment used to launch the app. Prefer `.venv` commands above. |
| `No module named pdf2image` | Ensure `pdf2image` is in requirements and installed in that environment. |
| `Unable to get page count. Is poppler installed and in PATH?` | Read the underlying exception. Poppler may be missing, unreachable or blocked; the wrapper message alone is not a diagnosis. |
| `WinError 4551` / Application Control block | Windows refused to run a required executable. Obtain an approved installation or authorized policy resolution; do not bypass device security. |
| Tesseract not found | The OCR executable is a separate install from `pytesseract`; check PATH or approved configuration. |
| PDF works locally but not on Community Cloud | Check Linux `packages.txt`, Python requirements and Windows-only executable paths. |
| Reconciliation seems slow | Check the progress stage. Scanned PDF rendering/OCR can dominate runtime even with candidate-based matching. |
| History disappeared after deployment | Check database location and storage persistence. Local session state does not replace durable storage. |
| Zero net gap but unresolved items remain | Opposite-signed differences can cancel. Review the gross unresolved value and individual items. |
| Generic reconciliation failure | Inspect the underlying exception during local debugging. Do not expose raw tracebacks or sensitive paths to public users. |

## Current limitations and roadmap

Before wider production use, priorities include:

- Authenticated users, roles and client-specific permissions.
- PostgreSQL-backed durable history with migration and restore tests.
- Safe, versioned report serialization: current history uses Python pickle and must only load trusted, app-owned data. Never import an untrusted history database.
- Manual pairing corrections and an append-only review audit trail.
- Consistent date-tolerance behaviour across matching engines.
- Source-period selection, opening-balance validation and explicit sign-orientation controls.
- Better extraction diagnostics, rejected-row reporting and broader PDF fixtures.
- Review-aware Excel exports, retention controls and upload/OCR resource limits.

Automated matches, suggested causes and report totals should always be checked against the source books and supporting evidence before any settlement or accounting adjustment.
