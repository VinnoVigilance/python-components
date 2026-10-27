"""Replay the real saved OCR snapshot; no inference/download/database needed."""

import json
import unittest
from copy import deepcopy
from pathlib import Path

from parsing.ocr.ocrRecordExtractor import OcrLayoutError, OcrRecordExtractor


ROOT = Path(__file__).resolve().parents[3]


class TestPdeaOcrLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = json.loads((ROOT / "config/ocrLayouts/pdea_most_wanted.json").read_text(encoding="utf-8"))
        cls.document = json.loads((
            ROOT / "test/fixtures/watchlist/pdea_most_wanted/ocr_document.json"
        ).read_text(encoding="utf-8"))

    def records(self):
        return OcrRecordExtractor(self.layout).extract(self.document)

    def test_all_sixteen_primary_names_match_the_reviewed_poster(self):
        # Gold names belong in tests, not production extractor code or layout.
        expected = [
            "WANG YA SHI", "HENRY TAN Y TAMAYO", "DENG XIAO LI", "ONG KIM TIE",
            "QUI YUN HAI", "JACKSON UY", "BENITO SZE", "LIN TSUNG HUAN",
            "RANDY CHUA Y NGO", "ONG SHIN PIN", "DING CAI HUI", "RONALD YAO Y CRUZ",
            "WEN JIN CAI", "CHUA LIN YEN", "CHEN JIAN LONG", "JUANITO LEE TY",
        ]
        self.assertEqual([row["primary_name"] for row in self.records()], expected)

    def test_duplicate_alias_line_does_not_create_an_extra_person(self):
        records = self.records()
        self.assertEqual(len(records), 16)
        matches = [r for r in records if r["primary_name"] == "BENITO SZE"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["raw_text"].count("BENITO SZE"), 2)

    def test_footer_dates_hotlines_and_president_are_not_person_records(self):
        raw_text = "\n".join(row["raw_text"] for row in self.records())
        for footer in ("President Gloria", "July 28", "PDEA Hotline", "CALL/TEXT"):
            self.assertNotIn(footer, raw_text)

    def test_uncertain_case_number_is_preserved_without_becoming_structured_data(self):
        last = self.records()[-2]
        self.assertIn("CC02-1576", last["raw_text"])
        self.assertNotIn("case_number", last)
        self.assertNotIn("aliases", last)

    def test_layout_positions_and_hashes_are_not_person_identifiers(self):
        for row in self.records():
            self.assertEqual(set(row), {"primary_name", "raw_text", "_ocr"})
            self.assertEqual(row["_ocr"]["source_sha256"], self.document["source_sha256"])
            self.assertEqual(row["_ocr"]["page_number"], 1)
            self.assertNotIn("source_record_id", row)
            self.assertNotIn("status", row)

    def test_score_review_flag_is_explicit_and_does_not_drop_the_name(self):
        flagged = [row for row in self.records() if row["_ocr"]["needs_review"]]
        self.assertEqual([r["primary_name"] for r in flagged], ["RONALD YAO Y CRUZ"])
        self.assertEqual(flagged[0]["_ocr"]["review_flags"][0]["reason"], "low_ocr_score")

    def test_missing_last_name_causes_failure_instead_of_fifteen_records(self):
        doc = deepcopy(self.document)
        page = doc["pages"][0]
        page["lines"] = [line for line in page["lines"] if line["text"] != "JUANITO LEE TY"]
        page["line_count"] = len(page["lines"])
        with self.assertRaisesRegex(OcrLayoutError, "Missing required"):
            OcrRecordExtractor(self.layout).extract(doc)


if __name__ == "__main__":
    unittest.main()
