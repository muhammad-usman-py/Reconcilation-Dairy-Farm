"""
src/extraction/ocr_extract.py

OCR-based extraction for image-only ledger PDFs (no embedded text layer).

Two ledger formats are supported out of the box:
  - QuickBooks-style "Account QuickReport" (single-line transactions)
  - KD Feeds "Business Partner Ledger" (multi-line transactions, grouped
    under a date-starting header line)

Approach:
  1. Rasterize each PDF page to an image (pdf2image, 300 DPI).
  2. Run pytesseract in bounding-box mode (image_to_data) to get every
     word's (text, left, top) position.
  3. Reconstruct table rows by clustering words by their 'top' (y)
     position, rather than trusting tesseract's automatic block/line
     segmentation — which mis-splits multi-column table layouts into
     separate blocks per column.
  4. Parse each ledger format's rows into a clean pandas DataFrame.

Add a new `parse_<format>(lines)` function for any additional ledger
layout you encounter, following the same pattern.
"""

import re
from pathlib import Path
import pandas as pd
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_path


_WINDOWS_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if _WINDOWS_TESSERACT.exists():
    pytesseract.pytesseract.tesseract_cmd = str(_WINDOWS_TESSERACT)


def pdf_to_lines(pdf_path, dpi=300, row_tolerance_px=12, poppler_path=None):
    """
    Convert a PDF into a flat list of (page_num, top, line_text) tuples,
    one per visual row, reconstructed from OCR word bounding boxes.

    row_tolerance_px: max vertical pixel distance between words to be
    considered part of the same row. Tune this if rows in your ledger
    are unusually tightly or loosely spaced (300 DPI assumed).

    poppler_path: pass this explicitly (e.g.
    r"C:\\Users\\Public\\poppler-26.07.0\\Library\\bin") if pdftoppm is
    not on your system PATH. Avoids needing PATH to be set up at all.
    """
    pages = convert_from_path(pdf_path, dpi=dpi, poppler_path=poppler_path)
    all_lines = []

    for page_num, img in enumerate(pages, start=1):
        data = pytesseract.image_to_data(img, output_type=Output.DICT)

        words = [
            {"text": data["text"][i].strip(), "left": data["left"][i], "top": data["top"][i]}
            for i in range(len(data["text"]))
            if data["text"][i].strip()
        ]
        words.sort(key=lambda w: w["top"])

        rows, current_row, current_top = [], [], None
        for w in words:
            if current_top is None or abs(w["top"] - current_top) <= row_tolerance_px:
                current_row.append(w)
                current_top = w["top"] if current_top is None else (current_top + w["top"]) / 2
            else:
                rows.append(current_row)
                current_row, current_top = [w], w["top"]
        if current_row:
            rows.append(current_row)

        for r in rows:
            r_sorted = sorted(r, key=lambda w: w["left"])
            raw_line = " ".join(w["text"] for w in r_sorted)
            # Fix a common OCR artifact: a stray space inserted before a
            # comma-thousands group, e.g. "2,937 ,480.00" -> "2,937,480.00"
            line = re.sub(r"(?<=\d)\s+(?=,\d)", "", raw_line)
            all_lines.append((page_num, r_sorted[0]["top"], line))

    return all_lines


def parse_kd_feeds(lines):
    """
    Parser for the KD Feeds 'BUSINESS PARTNER LEDGER' format, where each
    transaction spans multiple visual rows: a header row starting with a
    date (containing voucher no., debit, credit, balance), followed by
    1-3 narration continuation rows with no leading date.
    """
    date_re = re.compile(r"^\d{2}/\d{2}/\d{4}")
    num_re = r"\(?[\d,]+\.\d{2}\)?"

    records, current = [], None
    for _page_num, _top, line in lines:
        if date_re.match(line):
            if current:
                records.append(current)
            parts = line.split()
            nums = re.findall(num_re, line)
            debit, credit, balance = (nums[-3:] if len(nums) >= 3 else ["", "", ""])
            current = {
                "date": parts[0],
                # OCR sometimes reads dashes in voucher numbers as '~'
                "voucher": re.sub("~", "-", parts[1]) if len(parts) > 1 else "",
                "debit": debit,
                "credit": credit,
                "balance": balance,
                "narration": line,
            }
        elif current:
            current["narration"] += " " + line

    if current:
        records.append(current)

    return pd.DataFrame(records)


def parse_quickbooks(lines):
    """
    Parser for the QuickBooks-style 'Account QuickReport' format, where
    every transaction is a single row: Type, Date, [Num], Memo, Amount,
    Balance.
    """
    date_re = re.compile(r"\d{2}-\d{2}-\d{4}")
    # A monetary value normally has a thousands separator.  Keeping this
    # distinct from quantities/reference numbers prevents a memo value such as
    # "400 BAGS" from being mistaken for the transaction amount.
    money_re = re.compile(r"\(?\d{1,3}(?:,\d{3})+(?:\.\d{2})?\)?")

    records = []
    for _page_num, _top, line in lines:
        m = date_re.search(line)
        if not m:
            continue
        amounts = money_re.findall(line)
        # QuickBooks rows end with Amount then Balance.  OCR can attach an
        # amount to the memo text, but the comma-separated amount itself is
        # still recoverable from the end of the visual row.
        amount, balance = (amounts[-2:] if len(amounts) >= 2 else ["", ""])
        records.append({"date": m.group(), "line": line, "amount": amount, "balance": balance})

    return pd.DataFrame(records)


if __name__ == "__main__":
    import sys
    import os

    if len(sys.argv) != 3 or sys.argv[1] not in ("kd_feeds", "quickbooks"):
        print("Usage: python ocr_extract.py <kd_feeds|quickbooks> <path-to-pdf>")
        sys.exit(1)

    fmt, path = sys.argv[1], sys.argv[2]
    # Hardcode your poppler bin folder here if it's not on PATH, e.g.:
    # POPPLER_PATH = r"C:\Users\Public\poppler-26.07.0\Library\bin"
    POPPLER_PATH = r"C:\Users\Public\poppler-26.07.0\Library\bin"
    extracted_lines = pdf_to_lines(path, poppler_path=POPPLER_PATH)

    df = parse_kd_feeds(extracted_lines) if fmt == "kd_feeds" else parse_quickbooks(extracted_lines)
    print(df.to_string())

    out_dir = "data/extracted"
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    out_path = os.path.join(out_dir, f"{stem}.csv")
    df.to_csv(out_path, index=False)
    print(f"\nSaved {len(df)} rows to {out_path}")
