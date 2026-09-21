"""Format-specific readers: xlsx, docx, pdf.

Each reader's only job is to turn a document into label/value pairs plus the
plain text it saw. Alignment by meaning, normalisation and comparison all
happen downstream, so a new format never touches the comparison logic.

The contract is deliberately narrow: return what the document says, or raise
Unreadable with a reason a human can act on. A reader that guesses to avoid
raising would convert a reliability problem into a wrong answer.
"""
import io
import re

from .fields import ALIASES, canon_label, field_for_label

# Below this, a PDF page has no usable text layer and is a scan.
TEXT_LAYER_MIN_CHARS = 50


class Unreadable(Exception):
    """Document could not be turned into text."""


# Aliases longest-first, so "port of discharge (pod)" wins over "pod".
_ALIAS_BY_LENGTH = sorted(ALIASES, key=len, reverse=True)

# PDF templates prefix the summary row: "TOTAL Gross Wt (kgs): 131,322 KG".
_ROW_PREFIX = re.compile(r"^(total|sub ?total)\s+", re.I)


def split_known_label(line):
    """Split "Shipper APRIL FINE PAPER TRADING" into label and value.

    PDF text extraction flattens table cells into a single line with no
    delimiter, so there is nothing to split on. Instead of guessing at
    whitespace runs -- which breaks on "POL BUATAN, INDONESIA" and mangles
    any value containing two spaces -- we match the longest known label that
    the line actually starts with.
    """
    probe = _ROW_PREFIX.sub("", line).strip()
    lowered = probe.lower()
    for alias in _ALIAS_BY_LENGTH:
        if not lowered.startswith(alias):
            continue
        rest = probe[len(alias):]
        # Require a real boundary so "POL" does not swallow "POLAND".
        if rest and not rest[0].isspace() and rest[0] not in ":	":
            continue
        value = rest.lstrip(" :	")
        if value:
            return alias, value
    return None, None


def _pairs_to_fields(pairs):
    """Fold (label, value, where) triples into canonical fields; first wins.

    Returns (fields, inexact, sources).

    `inexact` names the fields whose label only matched by fallback. A value
    reached through a damaged header may not be the field we think it is,
    and the comparison needs that doubt.

    `sources` records where each value was read from -- the verbatim line
    and a human-readable locator. A reviewer who disagrees with a value
    should not have to reopen the attachment to see what the page actually
    said.
    """
    found, inexact, sources = {}, set(), {}
    for entry in pairs:
        label, value = entry[0], entry[1]
        where = entry[2] if len(entry) > 2 else None
        if not label or value is None:
            continue
        field, exact = field_for_label(str(label))
        if field and field not in found:
            text = str(value).strip()
            if text:
                found[field] = text
                if where:
                    sources[field] = where
                if not exact:
                    inexact.add(field)
    return found, inexact, sources


def from_xlsx(raw):
    """Spreadsheets here are label/value rows: column A labels, B the value.

    Later columns carry addresses and notes, which are not compared, so only
    the first non-empty cell after the label is taken.
    """
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    except Exception as exc:
        raise Unreadable(f"workbook would not open: {exc}") from exc

    pairs, lines = [], []
    for ws in wb.worksheets:
        for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
            cells = [c for c in row if c is not None and str(c).strip()]
            if not cells:
                continue
            joined = " | ".join(str(c) for c in cells)
            lines.append(joined)
            if len(cells) >= 2:
                pairs.append((cells[0], cells[1],
                              {"snippet": joined, "locator": f"{ws.title} row {n}"}))
    wb.close()

    if not lines:
        raise Unreadable("workbook contains no data")
    fields, inexact, sources = _pairs_to_fields(pairs)
    return fields, "\n".join(lines), {"source": "xlsx",
                                      "inexact_labels": sorted(inexact),
                                      "sources": sources}


def from_docx(raw):
    """Word documents put the fields in a two-column table; the title and
    reference numbers sit in paragraphs above it."""
    try:
        import docx
        doc = docx.Document(io.BytesIO(raw))
    except Exception as exc:
        raise Unreadable(f"document would not open: {exc}") from exc

    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    pairs = []
    for t, table in enumerate(doc.tables, start=1):
        for n, row in enumerate(table.rows, start=1):
            cells = [c.text.strip() for c in row.cells]
            if len(cells) >= 2 and cells[0]:
                joined = " | ".join(cells[:2]).replace("\n", " ")
                # Cells hold multi-line addresses; the value is the first line.
                pairs.append((cells[0], cells[1].splitlines()[0] if cells[1] else "",
                              {"snippet": joined,
                               "locator": f"table {t}, row {n}"}))
                lines.append(joined)

    # Some paragraphs are themselves "Label: value".
    for i, line in enumerate(list(lines), start=1):
        if ":" in line:
            label, _, value = line.partition(":")
            pairs.append((label, value,
                          {"snippet": line, "locator": f"paragraph {i}"}))

    if not lines:
        raise Unreadable("document contains no text")
    fields, inexact, sources = _pairs_to_fields(pairs)
    return fields, "\n".join(lines), {"source": "docx",
                                      "inexact_labels": sorted(inexact),
                                      "sources": sources}


def from_pdf(raw):
    """PDFs split three ways: a real text layer, a scan, or a broken file.

    Only the first is handled here. A scan is reported as such so the case
    can be escalated (or sent to OCR) rather than silently compared against
    an empty document, which would read as every field missing.
    """
    try:
        import pdfplumber
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception as exc:
        raise Unreadable(f"file is not a readable PDF: {exc}") from exc

    try:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as exc:
        raise Unreadable(f"PDF text extraction failed: {exc}") from exc
    finally:
        pdf.close()

    source, confidence = "text", None
    if len(text.strip()) < TEXT_LAYER_MIN_CHARS:
        # No text layer: this is a scan. Rasterise and OCR it rather than
        # declaring it unreadable.
        from .ocr import (OcrFailed, OcrUnavailable, looks_like_boilerplate,
                          ocr_pdf)
        try:
            text, confidence = ocr_pdf(raw)
        except OcrUnavailable as exc:
            raise Unreadable(f"PDF has no text layer (scanned image); {exc}") from exc
        except OcrFailed as exc:
            raise Unreadable(str(exc)) from exc
        if len(text.strip()) < TEXT_LAYER_MIN_CHARS:
            raise Unreadable("scanned page produced no readable text")
        text = "\n".join(
            ln for ln in text.splitlines() if not looks_like_boilerplate(ln))
        source = "ocr"

    if source == "ocr":
        # OCR damages the labels themselves, so exact matching is hopeless;
        # resolve each line to a field directly and skip the alias table.
        from .ocr import split_ocr_label
        fields, sources = {}, {}
        for n, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            field, value = split_ocr_label(line)
            if field and field not in fields and value:
                fields[field] = value
                sources[field] = {"snippet": line,
                                  "locator": f"OCR line {n}"}
        return fields, text, {"source": source, "confidence": confidence,
                              "inexact_labels": [], "sources": sources}

    pairs = []
    for n, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        where = {"snippet": line, "locator": f"line {n}"}
        label, value = split_known_label(line)
        if label:
            pairs.append((label, value, where))
        elif ":" in line:
            head, _, tail = line.partition(":")
            pairs.append((head, tail, where))
    fields, inexact, sources = _pairs_to_fields(pairs)
    return fields, text, {"source": source, "confidence": confidence,
                          "inexact_labels": sorted(inexact), "sources": sources}


def from_txt(raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Unreadable("attachment is not valid UTF-8 text") from exc
    if not text.strip():
        raise Unreadable("attachment is empty")

    pairs = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line or line[0].isspace() or ":" not in line:
            continue
        label, _, value = line.partition(":")
        if len(label) <= 60:
            pairs.append((label, value,
                          {"snippet": line.strip(), "locator": f"line {n}"}))
    fields, inexact, sources = _pairs_to_fields(pairs)
    return fields, text, {"source": "text", "inexact_labels": sorted(inexact),
                          "sources": sources}


READERS = {
    "txt": from_txt,
    "xlsx": from_xlsx,
    "xlsm": from_xlsx,
    "docx": from_docx,
    "pdf": from_pdf,
}


def read(raw, filename):
    """Dispatch on extension.

    Returns (fields, text, meta). `meta["source"]` says how the text was
    obtained -- notably "ocr" -- and `meta["confidence"]` carries the OCR
    engine's own score when there is one. Downstream uses both to decide how
    much to trust a comparison.
    """
    ext = filename.rsplit(".", 1)[-1].lower()
    reader = READERS.get(ext)
    if reader is None:
        raise Unreadable(f"no reader for .{ext} attachments")
    return reader(raw)
