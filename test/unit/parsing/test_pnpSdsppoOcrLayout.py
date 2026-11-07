"""Real four-page OCR geometry; no network, model download or database."""

import hashlib
import json
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from parsing.ocr.ocrRecordExtractor import OcrLayoutError, OcrRecordExtractor
from parsing.ocrParser import OcrParser


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "test/fixtures/watchlist/pnp_sdsppo_most_wanted"
LAYOUT = ROOT / "config/ocrLayouts/pnp_sdsppo_most_wanted.json"


class TestPnpSdsppoOcrLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = json.loads(LAYOUT.read_text(encoding="utf-8"))
        cls.pages = json.loads((FIXTURE / "ocr_pages.json").read_text(encoding="utf-8"))["pages"]
        source = FIXTURE / "selfcontained_source.html"
        cls.document = {
            "schema_version": 1, "document_type": "html",
            "source_path": str(source.resolve()),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_size_bytes": source.stat().st_size,
            "document_page_count": 4, "processed_page_count": 4,
            "selected_pages": [1, 2, 3, 4], "all_pages_processed": True,
            "pages": cls.pages,
        }

    def records(self, document=None):
        return OcrRecordExtractor(self.layout).extract(document or self.document)

    def test_eighteen_people_are_separated_as_five_four_five_four(self):
        records = self.records()
        self.assertEqual(len(records), 18)
        self.assertEqual(Counter(row["_ocr"]["page_number"] for row in records),
                         {1: 5, 2: 4, 3: 5, 4: 4})
        expected = [
            "SISON, Jose Maria", "VELASCO, Leo", "CABANATAN, Antonio",
            "CALUBID, Prudencio", "GUADIANA, Abadias/GUARDIANA, Abdias",
            "SULARTE, Myrna", "CASILAO, Eric Jun", "PURCRAY, Maria Luisa",
            "CAGADAS, Julia", "BOCALA, Ma Concepcion Araneta", "RENTON, Elsa",
            "TABOY, Rosita", "FERNANDEZ, Manuel", "DAVAL, Edilberto Reponte",
            "LEYSON , Rolando Jr", "MACO, Roderick Bensing", "BAYOT, Randy",
            "CABALLERA, Rolando",
        ]
        for row, name in zip(records, expected):
            # Gold names are test expectations, never hardcoded in the layout.
            text = " ".join(row["left_column_lines"])
            self.assertIn(name, text)

    def test_columns_keep_exact_labels_and_do_not_mix_adjacent_people(self):
        for row in self.records():
            left, right = row["left_column_lines"], row["right_column_lines"]
            for label in ("REGION", "NAME", "JOR NO.", "POSITION", "CC NO."):
                self.assertEqual(left.count(label), 1, (row["_ocr"], label))
            for label in ("ALIAS", "REWARD", "OFFENSE/S"):
                self.assertEqual(right.count(label), 1)
            self.assertEqual(sum(text.startswith("ISSUING COURT") for text in right), 1)
            self.assertNotIn("ALIAS", left)
            self.assertNotIn("NAME", right)

    def test_every_body_line_is_assigned_once_and_only_heading_is_excluded(self):
        records = self.records()
        actual = Counter((row["_ocr"]["page_number"], line["line_number"])
                         for row in records for line in row["_ocr"]["raw_lines"])
        expected = Counter((page["page_number"], line["line_number"])
                           for page in self.pages for line in page["lines"]
                           if not (page["page_number"] == 1 and line["line_number"] in (1, 2)))
        self.assertEqual(actual, expected)
        self.assertEqual(sum(actual.values()), 417)
        for row in records:
            fields = row["_ocr"]["fields"]
            assigned = Counter(line["line_number"] for field in fields.values() for line in field["lines"])
            self.assertEqual(assigned, Counter(line["line_number"] for line in row["_ocr"]["raw_lines"]))

    def test_wrapped_names_aliases_and_case_numbers_are_preserved(self):
        records = self.records()
        self.assertIn("Araneta", records[9]["left_column_lines"])
        self.assertIn("ROSA/OSANG/IPAY/ANA/SAMSI", records[10]["right_column_lines"])
        self.assertIn("7200-SN", records[6]["left_column_lines"])
        self.assertIn("10; 22625-22626", records[8]["left_column_lines"])

    def test_missing_portraits_do_not_drop_people(self):
        records = self.records()
        self.assertIn("CABANATAN, Antonio", records[2]["left_column_lines"])
        self.assertIn("CABALLERA, Rolando", records[-1]["left_column_lines"])

    def test_ocr_errors_are_preserved_and_low_scores_flagged_without_correction(self):
        records = self.records()
        self.assertIn("Myma Malaya/Emang", records[5]["right_column_lines"])
        self.assertIn("isa!ladoud go abeweg pue", records[5]["right_column_lines"])
        self.assertIn("C0", records[16]["left_column_lines"])
        for index, field in ((5, "right_column_lines"), (16, "left_column_lines")):
            self.assertTrue(records[index]["_ocr"]["needs_review"])
            self.assertTrue(any(flag["field"] == field for flag in records[index]["_ocr"]["review_flags"]))

    def test_geometry_and_joint_orders_are_not_person_ids_or_statuses(self):
        records = self.records()
        for row in records:
            self.assertEqual(set(row), {"left_column_lines", "right_column_lines", "raw_text", "_ocr"})
            self.assertEqual(row["_ocr"]["source_sha256"], self.document["source_sha256"])
        self.assertGreater(sum("14-2012" in row["left_column_lines"] for row in records), 1)

    def test_missing_or_partial_pages_fail_instead_of_returning_a_short_list(self):
        doc = deepcopy(self.document)
        doc.update(pages=doc["pages"][:3], selected_pages=[1, 2, 3],
                   processed_page_count=3, all_pages_processed=False)
        with self.assertRaises(OcrLayoutError):
            self.records(doc)

    def test_changed_page_shape_fails_before_record_extraction(self):
        doc = deepcopy(self.document)
        page = doc["pages"][1]
        page["image_size"] = [576, 800]
        page["region"] = [0, 0, 576, 800]
        with self.assertRaisesRegex(OcrLayoutError, "Aspect ratio"):
            self.records(doc)

    def test_changed_poster_heading_fails_instead_of_using_old_layout_silently(self):
        doc = deepcopy(self.document)
        doc["pages"][0]["lines"][0]["text"] = "OTHER POSTER"
        with self.assertRaisesRegex(OcrLayoutError, "Anchor mismatch"):
            self.records(doc)

    def test_existing_html_parser_produces_eighteen_records_and_image_provenance(self):
        by_size = {tuple(page["image_size"]): page for page in self.pages}

        def infer(image, **kwargs):
            lines = by_size[image.size]["lines"]
            return SimpleNamespace(txts=[line["text"] for line in lines],
                                   scores=[line["score"] for line in lines],
                                   boxes=[line["box"] for line in lines])

        backend = Mock(side_effect=infer)
        config = {"file_type": "html", "parser": {"mode": "ocr", "layout_path": str(LAYOUT)}}
        with patch("parsing.ocr.rapidOcrEngine.RapidOcrEngine._build_backend", return_value=backend):
            records = OcrParser().parse(FIXTURE / "selfcontained_source.html", config)
        self.assertEqual(backend.call_count, 4)
        self.assertEqual([row["raw_text"] for row in records],
                         [row["raw_text"] for row in self.records()])
        for row in records:
            page = row["_ocr"]["page_number"]
            image = (FIXTURE / f"sdsppo_{page:02d}.png").read_bytes()
            self.assertEqual(row["_ocr"]["rendering"]["source_image_sha256"], hashlib.sha256(image).hexdigest())
            self.assertEqual(row["_ocr"]["source_sha256"], self.document["source_sha256"])


if __name__ == "__main__":
    unittest.main()
