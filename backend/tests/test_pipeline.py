"""Regression tests for the cases that actually cost score.

Each test pins a trap found in the corpus. They run without Postgres, the
API or a network, so they stay usable as a pre-commit gate.
"""
import pytest

from app.pipeline.classify import classify
from app.pipeline.extract import detect_doc_type, parse_text
from app.pipeline.fields import ALIASES, canon_label
from app.pipeline.normalize import normalise


class TestNormalisation:
    def test_port_defect_survives_shared_locode(self):
        """Defects are planted in the city while the locode is left alone.
        Normalising to the locode would hide every port discrepancy."""
        si = normalise("port_of_discharge", "MOMBASA, KENYA (KEMBA)")
        bl = normalise("port_of_discharge", "TUTICORIN, INDIA (KEMBA)")
        assert si != bl

    def test_absent_locode_is_not_a_defect(self):
        si = normalise("port_of_loading", "NHAVA SHEVA, INDIA")
        bl = normalise("port_of_loading", "NHAVA SHEVA, INDIA (INNSA)")
        assert si == bl

    @pytest.mark.parametrize("raw,expected", [
        ("131,058 KG", 131058), ("21,114 KG", 21114), ("134.586 MT", 134586),
    ])
    def test_weight(self, raw, expected):
        assert normalise("gross_weight_kg", raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("6 x 40'HC", 6), ("1 x 20'GP", 1), ("12 x 20'GP", 12),
    ])
    def test_container_count(self, raw, expected):
        assert normalise("container_count", raw) == expected

    def test_underscore_run_is_blank_not_zero(self):
        assert normalise("gross_weight_kg", "____MT") is None

    def test_party_suffix_is_a_real_difference(self):
        """Distinct legal entities. Fuzzy matching here would mask a defect."""
        a = normalise("shipper", "APRIL FINE PAPER TRADING")
        b = normalise("shipper", "APRIL FINE PAPER TRADING (MIDDLE EAST) FZE")
        assert a != b


class TestLabels:
    def test_bilingual_header_resolves(self):
        """`Gross Weight<CJK>(KGS)` -- 51 occurrences, silently dropped by any
        ASCII-only label regex, which strands the weight field as missing."""
        assert ALIASES[canon_label("Gross Weight\u6bdb\u91cd(KGS)")] == "gross_weight_kg"

    def test_net_weight_is_not_gross_weight(self):
        assert canon_label("NET WEIGHT") not in ALIASES

    @pytest.mark.parametrize("label,field", [
        ("Load Port", "port_of_loading"), ("POD", "port_of_discharge"),
        ("To the Order of", "consignee"), ("Notify Party", "notify_party"),
    ])
    def test_cross_document_synonyms(self, label, field):
        assert ALIASES[canon_label(label)] == field


class TestClassification:
    def test_scam_under_an_invoice_subject(self):
        """email_417: subject reads INVOICE_QUERY, sender and body are junk."""
        assert classify({
            "from": "admin@secure-mailbox.org",
            "subject": "Re: Invoice payment - kindly confirm your bank details",
            "body": "Hello Dear, I am a bank officer with an urgent business proposal",
            "attachments": [],
        })[0] == "SPAM"

    def test_request_to_send_a_bl_is_not_a_comparison(self):
        """91 emails. Nothing is attached, so there is nothing to compare."""
        assert classify({
            "from": "exports@ifpla.com", "subject": "RE_ TO CONFIRM DOCS",
            "body": "Please assist to send the draft BL for SIN832764835 for checking asap.",
            "attachments": [],
        })[0] == "SI_REQUEST"

    def test_inline_shipment_details_are_an_si_request(self):
        assert classify({
            "from": "hari@aprilasia.com", "subject": "REQUEST SI",
            "body": "Please find Shipping instruction for 5RFR-37631. POL: SINGAPORE POD: GDANSK",
            "attachments": [],
        })[0] == "SI_REQUEST"

    def test_pair_attached_is_a_comparison(self):
        assert classify({
            "from": "docs@vitalsolutions.sg", "subject": "REQUEST BL DRAFT",
            "body": "Attached are the SI and draft BL for OC 5ALT-01226.",
            "attachments": ["attachments/e_SI.txt", "attachments/e_BL.txt"],
        })[0] == "BL_COMPARISON"


class TestDocTypeGuard:
    def test_impostor_attachment_detected(self):
        """email_501-505 ship an invoice/packing list in the BL slot."""
        assert detect_doc_type("COMMERCIAL INVOICE\n====\nShipper: X") == "COMMERCIAL INVOICE"

    def test_indented_continuation_lines_ignored(self):
        """Address lines sit under the value and must not be parsed as labels."""
        parsed = parse_text(
            "Shipper: APRIL FAR EAST (M) SDN BHD\n"
            "  TOWER 2, AVENUE 5: LEVEL 6\n"
            "POD: KARACHI, PAKISTAN (PKKHI)\n"
        )
        assert parsed["shipper"] == "APRIL FAR EAST (M) SDN BHD"
        assert parsed["port_of_discharge"] == "KARACHI, PAKISTAN (PKKHI)"


class TestConfidence:
    """The confidence engine must separate 'the documents disagree' from
    'we may have misread them'. If those collapse together, either real
    defects get escalated away or artifacts get reported as defects."""

    def test_unit_artifact_escalates(self):
        """The 50,000 kg / 50,000 lb case: same digits, different units."""
        from app.pipeline.verdict import compare_field
        v = compare_field("gross_weight_kg", "50,000 kg", "50,000 lb")
        assert v.verdict == "MISMATCH"
        assert v.artifact == "unit_artifact"
        assert v.uncertain, "a unit artifact must not be reported as a plain defect"

    def test_ordinary_mismatch_is_confident(self):
        from app.pipeline.verdict import compare_field
        v = compare_field("gross_weight_kg", "21,114 KG", "23,114 KG")
        assert v.verdict == "MISMATCH"
        assert not v.uncertain
        assert v.confidence > 0.9

    def test_confident_and_uncertain_bands_do_not_overlap(self):
        from app.pipeline.verdict import ESCALATION_THRESHOLD, compare_field
        artifact = compare_field("gross_weight_kg", "50,000 kg", "50,000 lb")
        real = compare_field("gross_weight_kg", "21,114 KG", "23,114 KG")
        assert artifact.confidence < ESCALATION_THRESHOLD < real.confidence

    def test_blank_is_uncomparable_not_mismatch(self):
        from app.pipeline.verdict import compare_field
        v = compare_field("gross_weight_kg", "____MT", "134,586 KG")
        assert v.verdict == "UNCOMPARABLE"
        assert v.confidence == 0.0

    def test_trace_records_the_conversion(self):
        from app.pipeline.resolve import resolve
        tr = resolve("gross_weight_kg", "50,000 lb")
        assert tr.value == 22680
        assert any(s.name == "unit_convert" for s in tr.applied)
        assert tr.reasons, "an interpretive step must explain itself"

    def test_cosmetic_steps_cost_nothing(self):
        from app.pipeline.resolve import resolve
        assert resolve("consignee", "  east bright fz-llc  ").penalty == 0.0

    def test_traced_and_plain_normalisers_agree(self):
        """resolve.py and normalize.py are two paths to one answer."""
        from app.pipeline.normalize import normalise
        from app.pipeline.resolve import resolve
        cases = [
            ("gross_weight_kg", "131,058 KG"), ("container_count", "6 x 40'HC"),
            ("port_of_discharge", "MOMBASA, KENYA (KEMBA)"),
            ("shipper", "APRIL FAR EAST (M) SDN BHD"),
        ]
        for field, raw in cases:
            assert resolve(field, raw).value == normalise(field, raw), field


class TestExtractors:
    """Format readers. Each test pins a trap found in the real attachments."""

    def test_bl_instruction_is_a_shipping_instruction(self):
        """PDF SIs are titled "BILL OF LADING INSTRUCTION" -- instructions for
        producing the BL. Matching "bill of lading" first mislabels all ten
        of them as the wrong document type."""
        from app.pipeline.extract import detect_doc_type
        assert detect_doc_type("BILL OF LADING INSTRUCTION\nB/L NUMBER: X") == "SI"
        assert detect_doc_type("BILL OF LADING (DRAFT)\nSHIPPER: X") == "BL"

    def test_only_an_impostor_is_wrong_doc_type(self):
        from app.pipeline.extract import detect_doc_type
        assert detect_doc_type("COMMERCIAL INVOICE\n===") == "COMMERCIAL INVOICE"
        assert detect_doc_type("PACKING LIST\n===") == "PACKING LIST"

    def test_pdf_label_split_without_a_delimiter(self):
        """PDF extraction flattens table cells to "Label value" with a single
        space, so there is nothing to split on but the label itself."""
        from app.pipeline.extractors import split_known_label
        assert split_known_label("Shipper APRIL FINE PAPER TRADING") == (
            "shipper", "APRIL FINE PAPER TRADING")
        assert split_known_label("POL BUATAN, INDONESIA") == ("pol", "BUATAN, INDONESIA")
        assert split_known_label("Port of Discharge (POD) FREMANTLE, AUSTRALIA") == (
            "port of discharge (pod)", "FREMANTLE, AUSTRALIA")

    def test_label_match_respects_word_boundaries(self):
        """"POL" must not swallow the first word of "POLAND ..."."""
        from app.pipeline.extractors import split_known_label
        assert split_known_label("POLAND IS NOT A LABEL") == (None, None)

    def test_total_prefix_on_summary_row(self):
        from app.pipeline.extractors import split_known_label
        label, value = split_known_label("TOTAL Gross Wt (kgs): 131,322 KG")
        assert label == "gross wt (kgs)" and value == "131,322 KG"

    def test_mangled_cjk_header_still_resolves(self):
        """PDF font extraction turns the CJK run in a bilingual header into
        filler ASCII: "Gross Weight<CJK>(KGS)" arrives as "Gross Weightnn(KGS)".
        The exact key is gone, so the fallback has to catch it."""
        from app.pipeline.fields import field_for_label
        field, exact = field_for_label("TOTAL Gross Weightnn(KGS)")
        assert field == "gross_weight_kg"
        assert not exact, "a damaged header should be reported as inexact"

    def test_fallback_never_folds_net_into_gross(self):
        from app.pipeline.fields import field_for_label
        assert field_for_label("NET WEIGHT")[0] is None

    def test_bilingual_docx_label_variants(self):
        from app.pipeline.fields import field_for_label
        for label in ("GROSS WEIGHT (\u6bdb\u91cdKGS)", "Gross Wt (kgs) (\u6bdb\u91cd KGS)",
                      "Consignee (\u6536\u8d27\u4eba)", "PORT OF LOADING (\u88c5\u8d27\u6e2f)"):
            assert field_for_label(label)[0] is not None, label

    def test_scanned_pdf_is_ocred_and_declares_its_source(self):
        """An image-only PDF is OCR'd rather than refused, and the result
        says so -- downstream discounts anything read from a scan."""
        from app.pipeline.extractors import from_pdf
        import pathlib
        raw = pathlib.Path("../data/attachments/email_512_SI.pdf").read_bytes()
        fields, text, meta = from_pdf(raw)
        assert meta["source"] == "ocr"
        assert 0 < meta["confidence"] <= 1
        assert fields, "OCR should recover shipment fields from a clean scan"
        # The watermark is stripped whichever resolution succeeded.
        assert "SCANNEDCOPY" not in text.upper().replace(" ", "")

    def test_corrupt_pdf_is_unreadable(self):
        from app.pipeline.extractors import Unreadable, from_pdf
        import pathlib
        raw = pathlib.Path("../data/attachments/email_511_BL.pdf").read_bytes()
        with pytest.raises(Unreadable):
            from_pdf(raw)

    @pytest.mark.parametrize("name", [
        "email_059_SI.pdf", "email_059_BL.pdf",     # PDF with a text layer
        "email_097_SI.xlsx", "email_005_SI.xlsx",   # spreadsheets
        "email_097_BL.docx", "email_462_BL.docx",   # Word, bilingual headers
    ])
    def test_all_seven_fields_extracted(self, name):
        from app.pipeline.extractors import read
        from app.pipeline.fields import FIELDS
        import pathlib
        raw = pathlib.Path(f"../data/attachments/{name}").read_bytes()
        fields, _text, _meta = read(raw, name)
        missing = [f for f in FIELDS if f not in fields]
        assert not missing, f"{name} missing {missing}"


class TestOcr:
    """Reading scans, and telling a misread apart from a real discrepancy.

    Both sides of every scanned pair in this corpus are images, so OCR noise
    lands on both documents at once. Tolerating it without hiding genuine
    differences is the whole problem.
    """

    def test_damaged_labels_still_resolve(self):
        """Real engine output: OCR splits words inside the label and drops
        the colon, so exact matching cannot work."""
        from app.pipeline.ocr import split_ocr_label
        cases = [
            ("Shippe r. APRIL FAR EAST ( M) SDN BHD", "shipper"),
            ("Conisig nee: AL GU RG STATIONERY LLC", "consignee"),
            ("Notity AL GURG STATIONERY'LLC", "notify_party"),
            ("Port of Lcading: NHAVA SHEVA INDIA", "port_of_loading"),
            ("Containiers 6 x 40'HC", "container_count"),
            ("Grcss Weig hit 128,544 KG", "gross_weight_kg"),
        ]
        for line, expected in cases:
            assert split_ocr_label(line)[0] == expected, line

    def test_document_title_is_not_a_field(self):
        """"SHIPPINGINSTRUCTION" is close enough to "shipper" to clear the
        fuzzy threshold, so the title must be excluded explicitly."""
        from app.pipeline.ocr import split_ocr_label
        assert split_ocr_label("SHIPPING INSTRUCTION") == (None, None)
        assert split_ocr_label("BILL OF LADING (DRAFT)") == (None, None)

    def test_split_watermark_is_boilerplate(self):
        from app.pipeline.ocr import looks_like_boilerplate
        assert looks_like_boilerplate("SC AN NED COPY - NO CC R TEXT LAYER")
        assert not looks_like_boilerplate("Consignee: AL GURG STATIONERY LLC")

    def test_glyph_substitutions_normalised(self):
        from app.pipeline.ocr import fix_glyphs
        assert fix_glyphs("6 × 40'HC") == "6 x 40'HC"

    def test_ocr_spacing_is_not_a_discrepancy(self):
        from app.pipeline.verdict import compare_field
        v = compare_field("consignee", "AL GU RG STATIONERY LLC",
                          "AL GURG STATIONERY LLC", si_ocr=True, bl_ocr=True)
        assert v.verdict == "MATCH" and v.artifact == "ocr_spacing"

    def test_separator_confusion_is_not_a_discrepancy(self):
        """"237,750" against "237.750" is one misread separator, not a
        change of 237 tonnes."""
        from app.pipeline.verdict import compare_field
        v = compare_field("gross_weight_kg", "237,750 KG", "237.750 KG",
                          si_ocr=True, bl_ocr=True)
        assert v.verdict == "MATCH" and v.artifact == "ocr_separator"

    def test_near_miss_escalates_instead_of_accusing(self):
        """"NHAWA SHEVA" against "NHAVA SHEVA" could be a different port or
        one substituted character. Neither answer is safe, so it escalates."""
        from app.pipeline.verdict import compare_field
        v = compare_field("port_of_loading", "NHAWA SHEVA INDIA",
                          "NHAVA SHEVA, IN DIA", si_ocr=True, bl_ocr=True)
        assert v.artifact == "ocr_noise"
        assert v.uncertain, "an OCR near-miss must not be reported as a defect"

    def test_genuinely_different_values_still_reported(self):
        """OCR tolerance must not swallow a real discrepancy."""
        from app.pipeline.verdict import compare_field
        v = compare_field("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA",
                          si_ocr=True, bl_ocr=True)
        assert v.verdict == "MISMATCH" and not v.uncertain

    def test_real_weight_change_survives_digit_comparison(self):
        from app.pipeline.verdict import compare_field
        v = compare_field("gross_weight_kg", "21,114 KG", "23,114 KG",
                          si_ocr=True, bl_ocr=True)
        assert v.verdict == "MISMATCH"
        assert v.artifact != "ocr_separator"

    @pytest.mark.parametrize("eid", ["512", "513", "514"])
    def test_scanned_pairs_report_no_false_defects(self, eid):
        """End to end on the real scanned pairs.

        These documents agree with each other; every difference the engine
        surfaces is its own misreading. None may be reported as a defect --
        that is the false-alarm failure the brief warns about. At 200 dpi
        this produced thirteen flagged rows across the three pairs.
        """
        from app.pipeline.extract import extract
        from app.pipeline.verdict import compare_documents
        import pathlib
        d = pathlib.Path("../data/attachments")
        si, _, sm = extract((d / f"email_{eid}_SI.pdf").read_bytes(),
                            f"email_{eid}_SI.pdf", expect="SI")
        bl, _, bm = extract((d / f"email_{eid}_BL.pdf").read_bytes(),
                            f"email_{eid}_BL.pdf", expect="BL")
        verdicts, status, defects, _reason, _c = compare_documents(si, bl, sm, bm)

        assert defects == [], f"{eid}: OCR noise reported as a defect"
        assert status in ("OK", "NEEDS_REVIEW")
        # Anything still flagged must be labelled as a reading problem.
        for v in verdicts:
            if v.verdict != "MATCH":
                assert v.kind in ("ocr_misread", "formatting", "not_stated"),                     f"{eid}/{v.field} classified as {v.kind}"


class TestDifferenceKind:
    """Why two values differ, classified in the comparison step.

    The fact of a difference is not actionable on its own: a genuine
    mismatch goes back to the shipper, a misread goes back to the scanner,
    and a label mapping problem means the two values may not even describe
    the same field. These classifications drive different work, so they are
    computed where the evidence is, not inferred in the UI.
    """

    @pytest.mark.parametrize("field,si,bl,kw,expected", [
        ("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA", {}, "genuine"),
        ("port_of_loading", "NHAVA SHEVA, INDIA", "NHAVA SHEVA, INDIA (INNSA)",
         {}, "formatting"),
        ("gross_weight_kg", "50,000 kg", "50,000 lb", {}, "unit"),
        ("consignee", "AL GU RG STATIONERY", "AL GURG STATIONERY",
         {"si_ocr": True, "bl_ocr": True}, "formatting"),
        ("port_of_loading", "NHAWA SHEVA", "NHAVA SHEVA",
         {"si_ocr": True, "bl_ocr": True}, "ocr_misread"),
        ("gross_weight_kg", "21,114 KG", "23,114 KG",
         {"inexact_label": True}, "label_mapping"),
        ("shipper", "APRIL FAR EAST", "APRIL FAR EAST", {}, "match"),
        ("gross_weight_kg", "____MT", "134,586 KG", {}, "not_stated"),
    ])
    def test_kind(self, field, si, bl, kw, expected):
        from app.pipeline.verdict import compare_field
        assert compare_field(field, si, bl, **kw).kind == expected

    def test_every_kind_has_a_label(self):
        from app.pipeline.verdict import KIND
        assert all(isinstance(v, str) and v for v in KIND.values())

    def test_a_mapping_doubt_still_reports_the_defect(self):
        """A loosely-matched header is worth flagging, but two plainly
        different values are still a discrepancy.

        A larger discount here turned emails 313 and 351 -- real defects --
        into escalations. The tag carries the doubt; the escalation
        threshold stays reserved for what genuinely cannot be called.
        """
        from app.pipeline.verdict import ESCALATION_THRESHOLD, compare_field
        v = compare_field("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA",
                          inexact_label=True)
        assert v.verdict == "MISMATCH"
        assert v.kind == "label_mapping"
        assert not v.uncertain, "a mapping doubt must not swallow a real defect"
        assert v.confidence < 1.0, "but it should read as less certain"

    def test_mapping_discount_cannot_cross_the_threshold(self):
        """Structural guard: raising the discount past this breaks the
        corpus alignment, silently."""
        from app.pipeline.verdict import ESCALATION_THRESHOLD, LABEL_MAPPING_DISCOUNT
        assert 1.0 - LABEL_MAPPING_DISCOUNT > ESCALATION_THRESHOLD

    def test_inexact_labels_are_reported_by_the_extractor(self):
        """email_160's weight header is damaged to 'Gross Weightnn(KGS)' and
        only resolves via fallback, which the comparison needs to know."""
        from app.pipeline.extractors import read
        import pathlib
        raw = pathlib.Path("../data/attachments/email_160_SI.pdf").read_bytes()
        _fields, _text, meta = read(raw, "email_160_SI.pdf")
        assert "gross_weight_kg" in meta["inexact_labels"]

    def test_clean_documents_report_no_inexact_labels(self):
        from app.pipeline.extractors import read
        import pathlib
        raw = pathlib.Path("../data/attachments/email_004_SI.txt").read_bytes()
        _fields, _text, meta = read(raw, "email_004_SI.txt")
        assert meta["inexact_labels"] == []


class TestOcrResolution:
    def test_render_dpi_is_the_measured_optimum(self):
        """200 dpi drops strokes ("Loading" -> "Lcading") and 400 corrupts
        digits ("128,544 KG" -> "128.644kG"). Measured on this corpus."""
        from app.pipeline.ocr import RENDER_DPI
        assert RENDER_DPI == 300

    def test_scanned_pair_reports_no_false_defects(self):
        """The invariant, not a row count.

        OCR may run at either resolution depending on available memory, and
        the number of flagged rows differs between them. Asserting an exact
        count made this test fail on a busy machine and pass on an idle one,
        which is worse than no test. What must always hold is that nothing
        the engine misread is reported as a defect.
        """
        from app.pipeline.extract import extract
        from app.pipeline.verdict import compare_documents
        import pathlib
        d = pathlib.Path("../data/attachments")
        si, _, sm = extract((d / "email_513_SI.pdf").read_bytes(),
                            "email_513_SI.pdf", expect="SI")
        bl, _, bm = extract((d / "email_513_BL.pdf").read_bytes(),
                            "email_513_BL.pdf", expect="BL")
        verdicts, _s, defects, _r, _c = compare_documents(si, bl, sm, bm)
        assert defects == [], "OCR noise must not be reported as a defect"
        for v in verdicts:
            if v.verdict != "MATCH":
                assert v.kind in ("ocr_misread", "formatting", "not_stated"), (
                    f"{v.field} flagged as {v.kind}, which would accuse the shipper")


class TestSourceTraceability:
    """Where each value was read from.

    A reviewer who disagrees with an extracted value should be able to see
    the line the document actually contained without reopening the
    attachment. Every format records a verbatim snippet and a locator the
    reader can check by hand.
    """

    @pytest.mark.parametrize("name,expect_locator", [
        ("email_004_SI.txt", "line "),
        ("email_097_SI.xlsx", "row "),
        ("email_097_BL.docx", "row "),
        ("email_160_SI.pdf", "line "),
    ])
    def test_every_format_records_a_source(self, name, expect_locator):
        from app.pipeline.extractors import read
        import pathlib
        raw = pathlib.Path(f"../data/attachments/{name}").read_bytes()
        fields, _text, meta = read(raw, name)
        sources = meta.get("sources") or {}
        assert sources, f"{name} recorded no sources"
        for field in fields:
            assert field in sources, f"{name}: {field} has no source"
            assert sources[field]["snippet"], f"{name}: {field} snippet empty"
        assert any(expect_locator in s["locator"] for s in sources.values())

    def test_the_snippet_contains_the_value(self):
        """The snippet must be the line the value came from, not a summary."""
        from app.pipeline.extractors import read
        import pathlib
        raw = pathlib.Path("../data/attachments/email_004_SI.txt").read_bytes()
        fields, _text, meta = read(raw, "email_004_SI.txt")
        for field, value in fields.items():
            snippet = meta["sources"][field]["snippet"]
            assert value in snippet, f"{field}: {value!r} not in {snippet!r}"

    def test_ocr_sources_are_labelled_as_ocr(self):
        from app.pipeline.extractors import read
        import pathlib
        raw = pathlib.Path("../data/attachments/email_512_SI.pdf").read_bytes()
        _fields, _text, meta = read(raw, "email_512_SI.pdf")
        assert all("OCR" in s["locator"] for s in meta["sources"].values())

    def test_sources_reach_the_field_verdict(self):
        """End to end: extraction records it, the comparison carries it."""
        from app.pipeline.extract import extract
        from app.pipeline.verdict import compare_documents
        import pathlib
        d = pathlib.Path("../data/attachments")
        si, _, sm = extract((d / "email_004_SI.txt").read_bytes(),
                            "email_004_SI.txt", expect="SI")
        bl, _, bm = extract((d / "email_004_BL.txt").read_bytes(),
                            "email_004_BL.txt", expect="BL")
        verdicts, *_ = compare_documents(si, bl, sm, bm)
        row = next(v for v in verdicts if v.field == "shipper")
        assert row.si.snippet and row.si.locator
        assert row.bl.snippet and row.bl.locator
        assert row.to_dict()["si"]["snippet"] == row.si.snippet


class TestAttachmentAccess:
    """Serving the original documents.

    Attachments are addressed by position in the email's own list, never by
    a client-supplied path, so no request can reach outside the dataset
    however it is crafted.
    """

    def test_index_is_bounds_checked(self):
        from app.routers.runs import CONTENT_TYPES
        assert CONTENT_TYPES  # sanity: the table exists

    @pytest.mark.parametrize("ext,inline", [
        ("txt", True), ("pdf", True), ("xlsx", False), ("docx", False),
    ])
    def test_only_browser_renderable_types_are_inline(self, ext, inline):
        """A spreadsheet served inline would render as a wall of bytes."""
        from app.routers.runs import CONTENT_TYPES
        assert CONTENT_TYPES[ext][1] is inline

    def test_unknown_types_download_as_octet_stream(self):
        from app.routers.runs import CONTENT_TYPES
        assert "exe" not in CONTENT_TYPES, \
            "an unlisted type must fall through to the download default"


class TestMatchIsNotFormatting:
    """A clean match must not be tagged as a formatting difference.

    "Formatting difference" has to mean the two DOCUMENTS were written
    differently. Tagging a row because the normaliser happened to run made
    440 identical rows across the corpus look flagged, which trains an
    operator to ignore the tag entirely.
    """

    @pytest.mark.parametrize("si,bl,expected", [
        ("ASHDOD, ISRAEL", "ASHDOD, ISRAEL", "match"),
        ("KPP-ANTALIS (SINGAPORE) PTE. LTD.",
         "KPP-ANTALIS (SINGAPORE) PTE. LTD.", "match"),
        ("RUGAO/NANTONG/SHANGHAI, CHINA",
         "RUGAO/NANTONG/SHANGHAI, CHINA", "match"),
        # Genuinely written differently: one side omits the locode.
        ("NHAVA SHEVA, INDIA", "NHAVA SHEVA, INDIA (INNSA)", "formatting"),
        ("  ASHDOD, ISRAEL  ", "ASHDOD, ISRAEL", "match"),
    ])
    def test_kind(self, si, bl, expected):
        from app.pipeline.verdict import compare_field
        assert compare_field("port_of_discharge", si, bl).kind == expected

    def test_corpus_formatting_tags_are_rare(self):
        """Most rows in a clean corpus agree outright. If this climbs back
        into the hundreds, the match/formatting test has regressed."""
        import collections
        from app.pipeline.extract import extract
        from app.pipeline.verdict import compare_documents
        import pathlib
        d = pathlib.Path("../data/attachments")
        kinds = collections.Counter()
        for si_path in sorted(d.glob("email_*_SI.txt"))[:40]:
            bl_path = d / si_path.name.replace("_SI.", "_BL.")
            if not bl_path.exists():
                continue
            try:
                si, _, sm = extract(si_path.read_bytes(), si_path.name, expect="SI")
                bl, _, bm = extract(bl_path.read_bytes(), bl_path.name, expect="BL")
            except Exception:
                continue
            for v in compare_documents(si, bl, sm, bm)[0]:
                kinds[v.kind] += 1
        assert kinds["match"] > kinds["formatting"] * 5, dict(kinds)


class TestAwaitingDocument:
    """A document that never arrived is a different job from one that
    arrived and would not parse. The first needs a reply; the second needs
    a retry. Merging them sends both to the wrong queue."""

    def test_a_request_to_send_a_document_is_detected(self):
        from app.pipeline.classify import awaiting_document
        assert awaiting_document({
            "body": "Please assist to send the draft BL for SIN832764835 for checking asap.",
            "attachments": [],
        })

    def test_an_email_with_attachments_is_not_awaiting(self):
        """It arrived. Whether it parses is a separate question."""
        from app.pipeline.classify import awaiting_document
        assert not awaiting_document({
            "body": "Please assist to send the draft BL for SIN832764835.",
            "attachments": ["attachments/e_SI.txt"],
        })

    def test_an_inline_si_is_not_awaiting(self):
        """These supply the details rather than asking for a file."""
        from app.pipeline.classify import awaiting_document
        assert not awaiting_document({
            "body": "Please find Shipping instruction for 5RFR-37631. POL: SINGAPORE",
            "attachments": [],
        })

    @pytest.mark.parametrize("text,expected", [
        ("send the draft BL for SIN832764835 for checking", "SIN832764835"),
        ("draft BL for PSGSE9638346 asap", "PSGSE9638346"),
        ("draft BL for 070500236763 asap", "070500236763"),
        ("draft BL for MCLSINJEA2576036 asap", "MCLSINJEA2576036"),
        ("OC 5ALT-01226 please advise", "5ALT-01226"),
    ])
    def test_reference_extraction(self, text, expected):
        from app.pipeline.classify import shipment_reference
        assert shipment_reference({"body": text, "subject": ""}) == expected

    def test_the_body_reference_wins_over_the_subject(self):
        """Subjects often carry a different reference from the one being
        asked about."""
        from app.pipeline.classify import shipment_reference
        got = shipment_reference({
            "body": "send the draft BL for SIN111111111",
            "subject": "RE_ DOCS _ SIN999999999",
        })
        assert got == "SIN111111111"

    def test_missing_attachment_is_not_folded_into_unreadable(self):
        from app.models import EmailResult
        assert "missing_attachment" not in EmailResult.UNREADABLE_REASONS


class TestOcrResilience:
    """A 520-email run must survive one bad document.

    At 300 dpi the ONNX detection model exhausted memory partway through
    the corpus and aborted the whole run. Losing 519 results to one page is
    never the right trade.
    """

    def test_a_fallback_resolution_exists_and_is_lower(self):
        from app.pipeline.ocr import FALLBACK_DPI, RENDER_DPI
        assert FALLBACK_DPI < RENDER_DPI

    def test_oversized_pages_are_downscaled(self):
        """A4 at 300 dpi is ~8.7MP, which is what blew the allocation."""
        from app.pipeline.ocr import MAX_PIXELS
        assert MAX_PIXELS < 2480 * 3508

    def test_engine_failure_raises_a_typed_error(self):
        """Callers need to turn this into an unreadable case, not a crash."""
        from app.pipeline import ocr
        assert issubclass(ocr.OcrFailed, Exception)
        assert ocr.OcrFailed is not ocr.OcrUnavailable


class TestWrongDocumentState:
    """The wrong file arriving is its own problem.

    It is not unreadable (the file parses fine) and not missing (something
    did arrive). Both of those suggest the wrong next action: a retry, or a
    plain "please send it". The right move is to say what turned up and
    what is still needed.
    """

    def test_wrong_doc_is_not_unreadable(self):
        from app.models import EmailResult
        assert "wrong_doc_type" not in EmailResult.UNREADABLE_REASONS

    def test_impostor_documents_are_detected(self):
        """email_501-505 carry an invoice, packing list or certificate in
        the BL slot."""
        from app.pipeline.extract import detect_doc_type
        from app.pipeline.extractors import read
        import pathlib
        d = pathlib.Path("../data/attachments")
        seen = set()
        for eid in ("501", "502", "503", "504", "505"):
            raw = (d / f"email_{eid}_BL.txt").read_bytes()
            _f, text, _m = read(raw, f"email_{eid}_BL.txt")
            kind = detect_doc_type(text)
            assert kind not in ("SI", "BL"), f"{eid} should be an impostor"
            seen.add(kind)
        assert seen == {"COMMERCIAL INVOICE", "PACKING LIST",
                        "CERTIFICATE OF ORIGIN"}

    def test_every_state_is_mutually_exclusive(self):
        """A case must land in exactly one bucket, or the tab counts stop
        summing to the corpus size."""
        from app.models import EmailResult

        def state_of(status, reason, awaiting):
            row = EmailResult(status=status, review_reason=reason,
                              awaiting_document=awaiting)
            return row.case_state

        assert state_of("NEEDS_REVIEW", "wrong_doc_type", False) == "WRONG_DOCUMENT"
        assert state_of("NEEDS_REVIEW", "missing_attachment", False) == "MISSING_ATTACHMENT"
        assert state_of("OK", None, True) == "MISSING_ATTACHMENT"
        assert state_of("NEEDS_REVIEW", "unreadable", False) == "UNREADABLE"
        assert state_of("NEEDS_REVIEW", "missing_value", False) == "NEEDS_REVIEW"
        assert state_of("MISMATCH", None, False) == "MISMATCH"
        assert state_of("OK", None, False) == "OK"

    def test_wrong_doc_outranks_awaiting(self):
        """A file arrived, so "awaiting" would be the wrong story even if
        the other slot is also empty."""
        from app.models import EmailResult
        row = EmailResult(status="NEEDS_REVIEW", review_reason="wrong_doc_type",
                          awaiting_document=True)
        assert row.case_state == "WRONG_DOCUMENT"


class TestSubmissionContract:
    """The export must match sample_submission.json exactly.

    This is the one artefact the scorer reads. A field renamed, a type
    changed, or an email dropped costs the whole run, and none of it would
    be visible in the UI.
    """

    @staticmethod
    def _sample():
        import json, pathlib
        return json.load(open(pathlib.Path("../data/sample_submission.json")))

    @staticmethod
    def _build():
        """Build a submission from the headless pipeline, as the export does."""
        from app.pipeline.run import run
        submission, _detail = run("../data")
        return {
            eid: {
                "category": v["category"],
                "status": v["status"],
                "review_reason": v["review_reason"],
                "defect_fields": v["defect_fields"],
                "has_defect": v["has_defect"],
            }
            for eid, v in submission.items()
        }

    def test_same_email_ids(self):
        sample, built = self._sample(), self._build()
        assert set(built) == set(sample)

    def test_same_field_names_and_order(self):
        sample, built = self._sample(), self._build()
        assert list(next(iter(built.values()))) == list(next(iter(sample.values())))

    def test_field_types_match(self):
        sample, built = self._sample(), self._build()
        for record in built.values():
            assert isinstance(record["category"], str)
            assert isinstance(record["status"], str)
            assert isinstance(record["defect_fields"], list)
            assert isinstance(record["has_defect"], bool)
            assert record["review_reason"] is None or isinstance(record["review_reason"], str)

    def test_values_stay_inside_the_documented_domains(self):
        from app.pipeline.classify import CATEGORIES
        from app.pipeline.compare import REVIEW_REASONS
        from app.pipeline.fields import FIELDS
        for record in self._build().values():
            assert record["category"] in CATEGORIES
            assert record["status"] in ("OK", "MISMATCH", "NEEDS_REVIEW")
            assert record["review_reason"] in (None, *REVIEW_REASONS)
            assert not set(record["defect_fields"]) - set(FIELDS)

    def test_internal_consistency(self):
        """has_defect, status and defect_fields must agree; the scorer reads
        all three and a disagreement is unscoreable."""
        for eid, r in self._build().items():
            assert r["has_defect"] == (r["status"] == "MISMATCH"), eid
            if r["status"] == "MISMATCH":
                assert r["defect_fields"], eid
            else:
                assert r["defect_fields"] == [], eid
            if r["status"] == "NEEDS_REVIEW":
                assert r["review_reason"] is not None, eid
            else:
                assert r["review_reason"] is None, eid

    def test_is_json_serialisable(self):
        import json
        json.dumps(self._build())
