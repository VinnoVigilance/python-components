"""Exercise the actual PDEA config and existing preprocessing engine offline."""

import json
import unittest
from copy import deepcopy
from pathlib import Path

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS

from parsing.ocr.ocrRecordExtractor import OcrRecordExtractor
from transforms.preProcessingEngine import PreProcessingEngine


ROOT = Path(__file__).resolve().parents[3]


class TestPdeaPreprocessing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = deepcopy(WATCHLIST_CONFIGS["PDEA_MOST_WANTED"])
        layout = json.loads((ROOT / "config/ocrLayouts/pdea_most_wanted.json").read_text(encoding="utf-8"))
        document = json.loads((ROOT / "test/fixtures/watchlist/pdea_most_wanted/ocr_document.json").read_text(encoding="utf-8"))
        cls.parsed = OcrRecordExtractor(layout).extract(document)

    def process(self, records=None):
        # The existing preprocessing engine mutates records; preserve test inputs.
        return PreProcessingEngine().preprocess(
            deepcopy(self.parsed if records is None else records),
            deepcopy(self.config["preprocessing"]),
        )

    def test_all_sixteen_people_have_distinct_internal_ids(self):
        records = self.process()
        self.assertEqual(len(records), 16)
        self.assertEqual(len({r["unique_id"] for r in records}), 16)
        self.assertTrue(all(r["unique_id"].startswith("PDEA-MOST-WANTED-") for r in records))

    def test_all_poster_records_are_individuals(self):
        records = deepcopy(self.parsed)
        records[0]["entity_type"] = "Entity"
        self.assertEqual({r["entity_type"] for r in self.process(records)}, {"Individual"})

    def test_name_raw_text_and_ocr_evidence_are_preserved(self):
        for before, after in zip(self.parsed, self.process()):
            self.assertEqual(after["primary_name"], before["primary_name"])
            self.assertEqual(after["raw_text"], before["raw_text"])
            self.assertEqual(after["_ocr"], before["_ocr"])

    def test_generated_internal_id_does_not_become_a_source_id(self):
        for record in self.process():
            self.assertNotIn("source_record_id", record)
            self.assertNotIn("Sources", record)

    def test_no_small_print_or_personal_attributes_are_inferred(self):
        expected = {"primary_name", "raw_text", "_ocr", "entity_type", "unique_id"}
        for record in self.process():
            self.assertEqual(set(record), expected)

    def test_reordering_cards_does_not_change_identity(self):
        def ids(records):
            return {r["primary_name"]: r["unique_id"] for r in records}
        self.assertEqual(ids(self.process()), ids(self.process(list(reversed(self.parsed)))))

    def test_image_hash_path_layout_and_card_position_do_not_change_identity(self):
        changed = deepcopy(self.parsed)
        for record in changed:
            record["raw_text"] += "\nChanged small print"
            record["_ocr"].update({
                "source_path": "different-day/new-poster.jpg",
                "source_sha256": "f" * 64,
                "pixel_sha256": "e" * 64,
                "layout_version": 2,
                "region_key": "different-card",
                "region": [0, 0, 100, 100],
            })
        self.assertEqual([r["unique_id"] for r in self.process()], [r["unique_id"] for r in self.process(changed)])

    def test_case_and_outer_whitespace_do_not_change_identity(self):
        original = self.process([self.parsed[0]])[0]
        variant = deepcopy(self.parsed[0])
        variant["primary_name"] = "  " + variant["primary_name"].lower() + "  "
        self.assertEqual(original["unique_id"], self.process([variant])[0]["unique_id"])

    def test_changed_name_gets_a_different_identity(self):
        # Name is the available fallback key, not a publisher-issued person ID.
        variant = deepcopy(self.parsed[0])
        variant["primary_name"] = "ANOTHER PERSON"
        self.assertNotEqual(self.process([self.parsed[0]])[0]["unique_id"], self.process([variant])[0]["unique_id"])

    def test_review_flag_does_not_drop_the_person_or_mark_human_approval(self):
        flagged = [r for r in self.process() if r["_ocr"]["needs_review"]]
        self.assertEqual([r["primary_name"] for r in flagged], ["RONALD YAO Y CRUZ"])
        self.assertEqual(flagged[0]["_ocr"]["review_flags"][0]["reason"], "low_ocr_score")
        self.assertNotIn("human_reviewed", flagged[0])

    def test_repeated_preprocessing_is_idempotent(self):
        once = self.process()
        self.assertEqual(self.process(once), once)


if __name__ == "__main__":
    unittest.main()
