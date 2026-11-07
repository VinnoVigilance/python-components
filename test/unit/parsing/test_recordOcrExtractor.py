"""Source-independent geometry, field cardinality and audit tests."""

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from parsing.ocr.ocrRecordExtractor import OcrLayoutError, OcrRecordExtractor


def line(number, text, left, top, width=20, height=10, score=0.98):
    return {"line_number": number, "text": text, "score": score,
            "box": [[left, top], [left + width, top],
                    [left + width, top + height], [left, top + height]]}


def example():
    layout = {
        "schema_version": 1, "layout_id": "EXAMPLE", "layout_version": 1,
        "expected_page_count": 1,
        "fields": {"name": {"box": [0, 0, 1, 0.4], "required": True,
                            "review_below_score": 0.95}},
        "pages": [{"page_number": 1, "reference_size": [100, 100],
                   "anchors": [{"box": [0, 0, 100, 20], "text_equals": "EXAMPLE"}],
                   "record_regions": [{"key": "left", "box": [0, 20, 50, 60]},
                                      {"key": "right", "box": [50, 20, 100, 60]}]}],
    }
    lines = [line(1, "EXAMPLE", 10, 0), line(2, "  ALPHA  ", 10, 25),
             line(3, "BETA", 60, 25), line(4, "uncertain detail?", 10, 45, score=0.01),
             line(5, "other detail", 60, 45)]
    page = {"schema_version": 1, "page_number": 1, "image_size": [100, 100],
            "region": [0, 0, 100, 100], "coordinate_space": "upright_image_pixels",
            "line_count": len(lines), "status": "TEXT_FOUND", "lines": lines,
            "pixel_sha256": "b" * 64, "engine": "example_ocr", "engine_version": "1"}
    doc = {"schema_version": 1, "source_sha256": "a" * 64, "document_page_count": 1,
           "processed_page_count": 1, "selected_pages": [1], "all_pages_processed": True,
           "pages": [page]}
    return layout, doc


class TestRecordOcrExtractor(unittest.TestCase):
    def setUp(self):
        self.layout, self.document = example()

    def extract(self):
        return OcrRecordExtractor(self.layout).extract(self.document)

    def test_records_preserve_raw_strings_and_uncertain_evidence(self):
        result = self.extract()
        self.assertEqual([r["name"] for r in result], ["  ALPHA  ", "BETA"])
        self.assertIn("uncertain detail?", result[0]["raw_text"])
        self.assertEqual(result[0]["_ocr"]["raw_lines"][1]["score"], 0.01)
        self.assertEqual(result[0]["_ocr"]["fields"]["name"]["lines"][0]["line_number"], 2)
        self.assertNotIn("source_record_id", result[0])
        self.assertNotIn("approved", result[0]["_ocr"])
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    def test_inputs_and_returned_evidence_do_not_share_mutable_state(self):
        saved = deepcopy(self.document)
        extractor = OcrRecordExtractor(self.layout)
        self.layout["fields"]["name"]["box"] = [0, 0, 1, 1]
        result = extractor.extract(self.document)
        result[0]["_ocr"]["raw_lines"][0]["text"] = "changed"
        result[0]["_ocr"]["fields"]["name"]["lines"][0]["text"] = "changed"
        self.assertEqual(self.document, saved)

    def test_reference_coordinates_scale_to_the_actual_page(self):
        page = self.document["pages"][0]
        page["image_size"], page["region"] = [200, 200], [0, 0, 200, 200]
        for row in page["lines"]:
            row["box"] = [[x * 2, y * 2] for x, y in row["box"]]
        result = self.extract()
        self.assertEqual([r["name"] for r in result], ["  ALPHA  ", "BETA"])
        self.assertEqual(result[0]["_ocr"]["region"], [0, 40, 100, 120])

    def test_aspect_ratio_change_is_rejected(self):
        self.document["pages"][0]["image_size"] = [200, 100]
        self.document["pages"][0]["region"] = [0, 0, 200, 100]
        with self.assertRaisesRegex(OcrLayoutError, "Aspect ratio"):
            self.extract()

    def test_header_anchor_must_match(self):
        self.document["pages"][0]["lines"][0]["text"] = "WRONG DOCUMENT"
        with self.assertRaisesRegex(OcrLayoutError, "Anchor mismatch"):
            self.extract()

    def test_anchor_normalizes_whitespace_and_can_ignore_case(self):
        self.layout["pages"][0]["anchors"][0]["case_sensitive"] = False
        self.document["pages"][0]["lines"][0]["text"] = "  example \n "
        self.assertEqual(len(self.extract()), 2)

    def test_missing_required_text_fails_instead_of_reducing_record_count(self):
        page = self.document["pages"][0]
        page["lines"] = [x for x in page["lines"] if x["line_number"] != 3]
        page["line_count"] -= 1
        with self.assertRaisesRegex(OcrLayoutError, "Missing required text"):
            self.extract()

    def test_multiple_name_lines_are_not_resolved_by_picking_highest_score(self):
        page = self.document["pages"][0]
        page["lines"].append(line(6, "ANOTHER CANDIDATE", 10, 25, score=0.999))
        page["line_count"] += 1
        with self.assertRaisesRegex(OcrLayoutError, "Ambiguous text"):
            self.extract()

    def test_whitespace_only_required_field_is_missing(self):
        self.document["pages"][0]["lines"][1]["text"] = " \n "
        with self.assertRaisesRegex(OcrLayoutError, "Missing required text"):
            self.extract()

    def test_low_score_flags_review_without_discarding_or_rewriting_text(self):
        self.document["pages"][0]["lines"][1]["score"] = 0.2
        result = self.extract()
        self.assertEqual(result[0]["name"], "  ALPHA  ")
        self.assertTrue(result[0]["_ocr"]["needs_review"])
        self.assertEqual(result[0]["_ocr"]["review_flags"][0]["line_numbers"], [2])
        self.assertFalse(result[1]["_ocr"]["needs_review"])

    def test_join_and_list_modes_use_configured_field_regions(self):
        self.layout["fields"]["details"] = {"box": [0, 0.5, 1, 1], "mode": "list"}
        result = self.extract()
        self.assertEqual(result[0]["details"], ["uncertain detail?"])
        self.layout["fields"]["details"].update(mode="join", separator=" | ")
        self.assertEqual(self.extract()[0]["details"], "uncertain detail?")

    def test_optional_empty_fields_have_explicit_empty_values(self):
        self.layout["fields"]["missing"] = {"box": [0, 0.4, 1, 0.5]}
        self.assertIsNone(self.extract()[0]["missing"])
        self.layout["fields"]["missing"]["mode"] = "list"
        self.assertEqual(self.extract()[0]["missing"], [])

    def test_half_open_center_selection_does_not_duplicate_boundary_lines(self):
        page = self.document["pages"][0]
        page["lines"].append(line(6, "BORDER", 45, 45, width=10))
        page["line_count"] += 1
        result = self.extract()
        self.assertNotIn("BORDER", result[0]["raw_text"])
        self.assertIn("BORDER", result[1]["raw_text"])

    def test_text_order_uses_geometry_not_input_list_order(self):
        before = self.extract()
        self.document["pages"][0]["lines"].reverse()
        after = self.extract()
        self.assertEqual(before, after)

    def test_multiple_pages_keep_page_and_region_provenance(self):
        self.document["document_page_count"] = self.document["processed_page_count"] = 2
        second = deepcopy(self.document["pages"][0])
        second["page_number"] = 2
        self.document["pages"].append(second)
        self.document["selected_pages"] = [1, 2]
        rule = deepcopy(self.layout["pages"][0])
        rule["page_number"] = 2
        self.layout["pages"].append(rule)
        self.layout["expected_page_count"] = 2
        result = self.extract()
        self.assertEqual([r["_ocr"]["page_number"] for r in result], [1, 1, 2, 2])

    def test_unconfigured_pages_cannot_be_silently_ignored(self):
        second = deepcopy(self.document["pages"][0])
        second["page_number"] = 2
        self.document.update(document_page_count=2, processed_page_count=2,
                             selected_pages=[1, 2], pages=[self.document["pages"][0], second])
        with self.assertRaisesRegex(OcrLayoutError, "match exactly"):
            self.extract()

    def test_partial_documents_are_rejected_unless_explicitly_allowed(self):
        self.document.update(document_page_count=2, all_pages_processed=False)
        self.layout["expected_page_count"] = 2
        with self.assertRaisesRegex(OcrLayoutError, "complete OCR"):
            self.extract()
        self.layout["require_all_pages"] = False
        result = self.extract()
        self.assertFalse(result[0]["_ocr"]["all_pages_processed"])
        self.assertIn({"reason": "partial_document"}, result[0]["_ocr"]["review_flags"])

    def test_expected_document_page_count_is_enforced(self):
        self.layout["expected_page_count"] = 2
        with self.assertRaisesRegex(OcrLayoutError, "expected_page_count"):
            self.extract()

    def test_layout_fingerprint_changes_when_field_definition_changes(self):
        first = OcrRecordExtractor(self.layout).layout_sha256
        self.layout["fields"]["name"]["review_below_score"] = 0.9
        self.assertNotEqual(first, OcrRecordExtractor(self.layout).layout_sha256)

    def test_invalid_layout_keys_geometry_and_fields_are_rejected(self):
        variants = []
        for field, value in (("schema_version", True), ("schema_version", 2),
                             ("layout_version", 0), ("require_all_pages", "yes"),
                             ("fields", {}), ("pages", [])):
            cfg = deepcopy(self.layout)
            cfg[field] = value
            variants.append(cfg)
        cfg = deepcopy(self.layout)
        cfg["unknown_typo"] = True
        variants.append(cfg)
        cfg = deepcopy(self.layout)
        cfg["fields"]["raw_text"] = cfg["fields"].pop("name")
        variants.append(cfg)
        for field, value in (("box", [0, 0, 1.1, 1]), ("mode", "first"),
                             ("required", 1), ("review_below_score", 1.1)):
            cfg = deepcopy(self.layout)
            cfg["fields"]["name"][field] = value
            variants.append(cfg)
        for cfg in variants:
            with self.subTest(cfg=cfg):
                with self.assertRaises(OcrLayoutError):
                    OcrRecordExtractor(cfg)

    def test_overlapping_or_duplicate_record_regions_are_rejected(self):
        for region in ({"key": "left", "box": [50, 20, 100, 60]},
                       {"key": "other", "box": [20, 20, 60, 60]}):
            cfg = deepcopy(self.layout)
            cfg["pages"][0]["record_regions"][1] = region
            with self.subTest(region=region):
                with self.assertRaises(OcrLayoutError):
                    OcrRecordExtractor(cfg)

    def test_invalid_document_metadata_cannot_claim_complete_processing(self):
        variants = []
        for key, value in (("schema_version", True), ("source_sha256", "bad"),
                           ("processed_page_count", True), ("selected_pages", [True]),
                           ("all_pages_processed", False)):
            doc = deepcopy(self.document)
            doc[key] = value
            variants.append(doc)
        for key, value in (("coordinate_space", "pdf_points"),
                           ("region", [0, 0, 50, 100]), ("schema_version", True),
                           ("line_count", 4), ("status", "FAILED")):
            doc = deepcopy(self.document)
            doc["pages"][0][key] = value
            variants.append(doc)
        for doc in variants:
            with self.subTest(doc=doc):
                with self.assertRaises(OcrLayoutError):
                    OcrRecordExtractor(self.layout).extract(doc)

    def test_invalid_ocr_text_scores_and_geometry_are_rejected(self):
        for key, value in (("text", None), ("score", float("nan")), ("score", 2),
                           ("box", [[0, 0]]), ("line_number", 1)):
            doc = deepcopy(self.document)
            doc["pages"][0]["lines"][1][key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaises(OcrLayoutError):
                    OcrRecordExtractor(self.layout).extract(doc)

    def test_extractor_does_not_require_ocr_models_or_database_modules(self):
        code = (
            "import sys; sys.modules['rapidocr']=None; sys.modules['onnxruntime']=None; "
            "sys.modules['psycopg2']=None; "
            "from parsing.ocr.ocrRecordExtractor import OcrRecordExtractor"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
