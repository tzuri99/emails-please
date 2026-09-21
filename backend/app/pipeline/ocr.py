"""Reading scanned pages.

A scanned SI/BL has no text layer, so the page is rasterised and passed to an
OCR engine. What comes back is never clean: in this corpus the engine returns
`Shippe r.` for `Shipper:`, `AL GU RG` for `AL GURG`, `Containiers` for
`Containers`, and a Unicode multiplication sign where the document had an x.

The important design decision is what to do with that noise. We do NOT clean
it away silently: the OCR text is kept verbatim on the trace so a reviewer
sees exactly what the engine read, and the fact that a value came from OCR is
carried downstream so the comparison can discount it. Tidying the text and
forgetting where it came from would turn a reading problem into a confident
wrong answer.

The engine is pluggable. The default runs locally with no API key; a
vision-capable LLM can be substituted by setting `ENGINE`.
"""
import logging
import re

log = logging.getLogger(__name__)

# Measured on this corpus: 200 dpi drops strokes ("Loading" -> "Lcading",
# "Shipper:" -> "Shippe r."), and 400 over-sharpens until words merge and
# digits corrupt ("128,544 KG" -> "128.644kG"). 300 reads cleanest.
RENDER_DPI = 300

# A4 at 300 dpi is ~8.7 megapixels and the detection model allocates in
# proportion. Across a 520-email run that exhausts memory and the ONNX
# session dies with "bad allocation" partway through.
#
# The budget is set at roughly the pixel count of a native 200 dpi page,
# but the page is still RENDERED at 300 and then downscaled. That
# supersampling is measurably better than rendering at 200 directly:
#
#     native 200 dpi   -> "Port of Lcading", "G ross Weight 128,544 KG"
#     300 dpi -> 4 MP  -> "Shipper:",        "Gross Weight 128,544 KG"
#
# so the quality the resolution choice was made for survives at a memory
# footprint that does not fail mid-corpus.
FALLBACK_DPI = 200
MAX_PIXELS = 4_000_000

# Characters OCR substitutes for their ASCII equivalents.
GLYPH_FIXES = {
    "×": "x",   # multiplication sign -> x, as in "6 x 40'HC"
    "’": "'",   # curly apostrophe
    "‘": "'",
    "“": '"',
    "”": '"',
    "–": "-",   # en dash
    "—": "-",
}

_ENGINE = None


class OcrUnavailable(Exception):
    """No OCR engine is installed in this environment."""


class OcrFailed(Exception):
    """The engine ran but could not process this document."""


def _engine():
    """Load the OCR engine once; model init is the expensive part."""
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:      # pragma: no cover - environment dependent
            raise OcrUnavailable(
                "no OCR engine installed (pip install rapidocr-onnxruntime)") from exc
        _ENGINE = RapidOCR()
    return _ENGINE


def fix_glyphs(text):
    for bad, good in GLYPH_FIXES.items():
        text = text.replace(bad, good)
    return text


def read_page_images(raw, dpi=RENDER_DPI):
    """Rasterise a PDF, yielding one page image at a time.

    A generator rather than a list: holding every page of a multi-page
    scan in memory at once is what pushes the OCR session over its
    allocation limit. Pages still above the pixel budget are downscaled,
    because a slightly softer image reads far better than no image.
    """
    import io

    import pdfplumber
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            image = page.to_image(resolution=dpi).original
            width, height = image.size
            if width * height > MAX_PIXELS:
                scale = (MAX_PIXELS / (width * height)) ** 0.5
                image = image.resize((max(1, int(width * scale)),
                                      max(1, int(height * scale))))
            yield image


def _ocr_at(raw, dpi):
    """One OCR pass over a PDF at the given resolution."""
    import numpy as np

    engine = _engine()
    lines, scores = [], []

    for image in read_page_images(raw, dpi):
        array = np.array(image)
        try:
            result, _ = engine(array)
        finally:
            del array          # release the page before rendering the next
        if not result:
            continue
        # Sort by vertical position: the engine does not guarantee reading order.
        for row in sorted(result, key=lambda r: r[0][0][1]):
            text, score = str(row[1]).strip(), float(row[2])
            if text:
                lines.append(fix_glyphs(text))
                scores.append(score)

    if not lines:
        return "", 0.0
    return "\n".join(lines), sum(scores) / len(scores)


def ocr_pdf(raw):
    """OCR a scanned PDF, degrading resolution rather than giving up.

    Returns (text, confidence), the confidence being the engine's mean
    score across recognised lines -- a real measure of how the page read.

    300 dpi reads measurably best but is memory-hungry, and on a long run
    the ONNX session can fail to allocate. Rather than losing the document,
    it is retried at a resolution that fits. Quality is worse there, so
    this is a fallback and never the default.
    """
    try:
        return _ocr_at(raw, RENDER_DPI)
    except Exception as exc:                 # allocation failures included
        log.warning("OCR at %d dpi failed (%s); retrying at %d dpi",
                    RENDER_DPI, type(exc).__name__, FALLBACK_DPI)
    try:
        return _ocr_at(raw, FALLBACK_DPI)
    except Exception as exc:
        raise OcrFailed(f"OCR failed at {RENDER_DPI} and {FALLBACK_DPI} dpi: {exc}") from exc


def looks_like_boilerplate(line):
    """Watermarks and scan banners are not shipment data.

    Tested against the de-spaced line, because OCR breaks the watermark up
    too: "SCANNED COPY" comes back as "SC AN NED COPY".
    """
    squashed = "".join(line.split()).lower()
    return bool(re.search(r"scannedcopy|nocc?rtextlayer|page\d+of\d+", squashed))


# Field keywords for matching a label that OCR has damaged. Deliberately a
# small, mutually distinctive set: these seven are easy to tell apart even
# after several character errors, so a loose threshold is safe here in a way
# it would not be against the full alias list.
OCR_LABEL_KEYWORDS = [
    ("portofdischarge", "port_of_discharge"),
    ("dischargeport", "port_of_discharge"),
    ("portofloading", "port_of_loading"),
    ("loadport", "port_of_loading"),
    ("noofcontainers", "container_count"),
    ("totalcontainers", "container_count"),
    ("containercount", "container_count"),
    ("containers", "container_count"),
    ("grossweight", "gross_weight_kg"),
    ("grosswt", "gross_weight_kg"),
    ("notifyparty", "notify_party"),
    ("consignee", "consignee"),
    ("shipper", "shipper"),
    ("notify", "notify_party"),
    ("pod", "port_of_discharge"),
    ("pol", "port_of_loading"),
]

# Below this the leading text is not a damaged version of the keyword.
LABEL_MATCH_THRESHOLD = 0.75


def _despace_map(line):
    """Return (squashed_lowercase, index_in_original_for_each_char)."""
    chars, positions = [], []
    for i, ch in enumerate(line):
        if not ch.isspace():
            chars.append(ch.lower())
            positions.append(i)
    return "".join(chars), positions


def split_ocr_label(line):
    """Split an OCR'd line into (field, value) despite a damaged label.

    OCR splits words and substitutes characters inside the label itself --
    "Gross Weight" arrives as "Grcss Weig hit", "Containers" as
    "Containiers" -- and frequently drops the colon. Matching is therefore
    done on the de-spaced line against a short keyword list, allowing a few
    character errors, and the value is whatever follows in the original text.
    """
    from difflib import SequenceMatcher

    squashed, positions = _despace_map(line)
    if not squashed:
        return None, None

    # The document's own title is not a field. "SHIPPINGINSTRUCTION" is
    # close enough to "shipper" to clear the threshold, so it is excluded
    # before matching rather than left to the ratio to reject.
    if squashed.startswith(("shippinginstruction", "billoflading")):
        return None, None

    best = None
    for keyword, field in OCR_LABEL_KEYWORDS:
        # Compare against leading substrings around the keyword's length;
        # a dropped or doubled character shifts the boundary either way.
        for length in (len(keyword) - 1, len(keyword), len(keyword) + 1):
            if length <= 0 or length > len(squashed):
                continue
            ratio = SequenceMatcher(None, keyword, squashed[:length]).ratio()
            if ratio >= LABEL_MATCH_THRESHOLD and (best is None or ratio > best[0]):
                best = (ratio, field, length)

    if best is None:
        return None, None

    _ratio, field, consumed = best
    start = positions[consumed - 1] + 1 if consumed <= len(positions) else len(line)
    value = line[start:].lstrip(" :.\t-")
    return (field, value) if value else (None, None)
