"""Position-aware OCR helpers and parsers for ledger documents.

PDF pages and directly uploaded images both become visual lines. Known layouts
retain dedicated parsers, while a conservative debit/credit parser handles
ordinary tabular ledgers whose transactions start with a date and end with
Debit, Credit and Balance values.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageOps
from pytesseract import Output


_WINDOWS_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
if _WINDOWS_TESSERACT.exists():
    pytesseract.pytesseract.tesseract_cmd = str(_WINDOWS_TESSERACT)

_DATE_AT_START = re.compile(r"^\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b")
_MONEY = re.compile(r"(?<!\w)\(?[-+]?\d[\d,]*(?:\.\d{2})\)?(?:\s*(?:CR|DR))?", re.IGNORECASE)


def _text(line: tuple) -> str:
    return str(line[2])


def _confidence(line: tuple) -> float:
    return float(line[3]) if len(line) > 3 else 100.0


def image_to_lines(
    image: Image.Image,
    *,
    page_num: int = 1,
    upscale: bool = True,
    row_tolerance_px: int | None = None,
) -> list[tuple[int, int, str, float]]:
    """OCR a page image into ``(page, y, text, confidence)`` rows."""
    prepared = ImageOps.exif_transpose(image).convert("L")
    if upscale and prepared.width < 2200:
        scale = min(2.5, 2200 / max(prepared.width, 1))
        prepared = prepared.resize(
            (int(prepared.width * scale), int(prepared.height * scale)),
            Image.Resampling.LANCZOS,
        )
    prepared = ImageOps.autocontrast(prepared)
    tolerance = row_tolerance_px or max(10, int(prepared.height * 0.012))
    data = pytesseract.image_to_data(prepared, output_type=Output.DICT, config="--psm 6")

    words = []
    for index, value in enumerate(data["text"]):
        value = str(value).strip()
        if not value:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        words.append({
            "text": value,
            "left": int(data["left"][index]),
            "top": int(data["top"][index]),
            "confidence": confidence,
        })
    words.sort(key=lambda word: word["top"])

    rows: list[list[dict]] = []
    current: list[dict] = []
    current_top: float | None = None
    for word in words:
        if current_top is None or abs(word["top"] - current_top) <= tolerance:
            current.append(word)
            current_top = word["top"] if current_top is None else (current_top + word["top"]) / 2
        else:
            rows.append(current)
            current = [word]
            current_top = word["top"]
    if current:
        rows.append(current)

    lines = []
    for row in rows:
        ordered = sorted(row, key=lambda word: word["left"])
        raw = " ".join(word["text"] for word in ordered)
        text = re.sub(r"(?<=\d)\s+(?=,\d)", "", raw)
        usable = [word["confidence"] for word in ordered if word["confidence"] >= 0]
        mean_confidence = sum(usable) / len(usable) if usable else 0.0
        lines.append((page_num, ordered[0]["top"], text, mean_confidence))
    return lines


def image_file_to_lines(path: str | Path) -> list[tuple[int, int, str, float]]:
    """Load and OCR a PNG/JPEG ledger image."""
    with Image.open(path) as image:
        return image_to_lines(image)


def pdf_to_lines(pdf_path, dpi=300, row_tolerance_px=12, poppler_path=None):
    """Rasterize a PDF and OCR its pages into positioned visual rows."""
    pages = convert_from_path(pdf_path, dpi=dpi, poppler_path=poppler_path)
    lines = []
    for page_num, image in enumerate(pages, start=1):
        lines.extend(
            image_to_lines(
                image,
                page_num=page_num,
                upscale=False,
                row_tolerance_px=row_tolerance_px,
            )
        )
    return lines


def detect_ledger_format(lines) -> str:
    """Classify a ledger layout without asking the user to name its software."""
    header = " ".join(_text(line).upper() for line in lines[:50])
    if "ACCOUNT QUICKREPORT" in header:
        return "quickbooks"
    if "BUSINESS PARTNER LEDGER" in header or ("KD FEEDS" in header and "GENERAL LEDGER" in header):
        return "kd_feeds"
    if "DEBIT" in header and "CREDIT" in header:
        return "generic_debit_credit"

    dated_rows = 0
    debit_credit_rows = 0
    for line in lines:
        text = _text(line)
        if not _DATE_AT_START.match(text):
            continue
        dated_rows += 1
        if len(_MONEY.findall(text)) >= 3:
            debit_credit_rows += 1
    if dated_rows and debit_credit_rows / dated_rows >= 0.7:
        return "generic_debit_credit"
    raise ValueError(
        "This ledger layout could not be identified safely. It needs visible "
        "dated rows and Debit/Credit/Balance columns, or a supported layout."
    )


def parse_kd_feeds(lines):
    """Parse the KD Feeds Business Partner Ledger layout."""
    date_re = re.compile(r"^\d{2}/\d{2}/\d{4}")
    num_re = r"\(?[\d,]+\.\d{2}\)?"
    records, current = [], None
    for line_data in lines:
        line = _text(line_data)
        if date_re.match(line):
            if current:
                records.append(current)
            parts = line.split()
            nums = re.findall(num_re, line)
            debit, credit, balance = nums[-3:] if len(nums) >= 3 else ("", "", "")
            current = {
                "date": parts[0],
                "voucher": re.sub("~", "-", parts[1]) if len(parts) > 1 else "",
                "debit": debit,
                "credit": credit,
                "balance": balance,
                "narration": line,
                "ocr_confidence": _confidence(line_data),
            }
        elif current and not re.search(r"(?:TOTAL RECORDS|END OF REPORT)", line, re.IGNORECASE):
            current["narration"] += " " + line
    if current:
        records.append(current)
    return pd.DataFrame(records)


def parse_quickbooks(lines):
    """Parse QuickBooks Account QuickReport rows."""
    date_re = re.compile(r"\d{2}-\d{2}-\d{4}")
    money_re = re.compile(r"\(?\d{1,3}(?:,\d{3})+(?:\.\d{2})?\)?")
    records = []
    for line_data in lines:
        line = _text(line_data)
        match = date_re.search(line)
        if not match:
            continue
        amounts = money_re.findall(line)
        amount, balance = amounts[-2:] if len(amounts) >= 2 else ("", "")
        records.append({
            "date": match.group(),
            "line": line,
            "amount": amount,
            "balance": balance,
            "ocr_confidence": _confidence(line_data),
        })
    return pd.DataFrame(records)


def parse_debit_credit(lines):
    """Parse a conservative date-first Debit/Credit/Balance ledger table."""
    records: list[dict] = []
    current: dict | None = None
    dated_rows = 0
    rejected_rows = 0

    for line_data in lines:
        line = _text(line_data).strip()
        date_match = _DATE_AT_START.match(line)
        if date_match:
            dated_rows += 1
            if current:
                records.append(current)
                current = None
            monetary = list(_MONEY.finditer(line))
            if len(monetary) < 3:
                rejected_rows += 1
                continue
            debit_match, credit_match, balance_match = monetary[-3:]
            prefix = line[:debit_match.start()].strip()
            parts = prefix.split()
            reference = parts[1] if len(parts) > 1 else ""
            narration = " ".join(parts[2:]) if len(parts) > 2 else prefix
            current = {
                "date": date_match.group(1),
                "reference": reference,
                "narration": narration,
                "debit": debit_match.group(),
                "credit": credit_match.group(),
                "balance": balance_match.group(),
                "ocr_confidence": _confidence(line_data),
            }
        elif current:
            if re.search(r"^(?:TOTAL|END OF|\*+END)", line, re.IGNORECASE):
                records.append(current)
                current = None
            elif line and not re.search(r"\b(?:DATE|DEBIT|CREDIT|BALANCE)\b", line, re.IGNORECASE):
                current["narration"] = f"{current['narration']} {line}".strip()
                current["ocr_confidence"] = min(current["ocr_confidence"], _confidence(line_data))
    if current:
        records.append(current)

    result = pd.DataFrame(records)
    result.attrs["dated_rows"] = dated_rows
    result.attrs["rejected_rows"] = rejected_rows
    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3 or sys.argv[1] not in {"kd_feeds", "quickbooks", "generic"}:
        print("Usage: python ocr_extract.py <kd_feeds|quickbooks|generic> <path-to-pdf>")
        raise SystemExit(1)
    fmt, source_path = sys.argv[1], sys.argv[2]
    extracted_lines = pdf_to_lines(source_path, poppler_path=None)
    parsers = {"kd_feeds": parse_kd_feeds, "quickbooks": parse_quickbooks, "generic": parse_debit_credit}
    print(parsers[fmt](extracted_lines).to_string(index=False))
