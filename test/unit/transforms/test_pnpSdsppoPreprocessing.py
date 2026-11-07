"""Exercise real saved OCR columns with the source's installed config."""

import hashlib
import json
import unittest
from copy import deepcopy
from pathlib import Path

from parsing.ocr.ocrRecordExtractor import OcrRecordExtractor
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from transforms.preProcessingEngine import PreProcessingEngine


ROOT = Path(__file__).resolve().parents[3]


class TestPnpSdsppoPreprocessing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = ROOT / "test/fixtures/watchlist/pnp_sdsppo_most_wanted"
        pages = json.loads((fixture / "ocr_pages.json").read_text(encoding="utf-8"))["pages"]
        document = {
            "schema_version": 1, "source_sha256": hashlib.sha256(
                (fixture / "selfcontained_source.html").read_bytes()).hexdigest(),
            "document_page_count": 4, "processed_page_count": 4,
            "selected_pages": [1, 2, 3, 4], "all_pages_processed": True, "pages": pages,
        }
        layout = json.loads((ROOT / "config/ocrLayouts/pnp_sdsppo_most_wanted.json").read_text(encoding="utf-8"))
        cls.raw = OcrRecordExtractor(layout).extract(document)
        cls.config = WATCHLIST_CONFIGS["PNP_SDSPPO_MOST_WANTED"]

    def processed(self, raw=None):
        return PreProcessingEngine().preprocess(
            deepcopy(self.raw if raw is None else raw), deepcopy(self.config["preprocessing"]))

    def test_eighteen_names_and_wrapped_name_are_extracted(self):
        rows = self.processed()
        self.assertEqual(len(rows), 18)
        self.assertEqual([row["primary_name"] for row in rows], [
            "SISON, Jose Maria", "VELASCO, Leo", "CABANATAN, Antonio",
            "CALUBID, Prudencio", "GUADIANA, Abadias", "SULARTE, Myrna",
            "CASILAO, Eric Jun", "PURCRAY, Maria Luisa", "CAGADAS, Julia",
            "BOCALA, Ma Concepcion Araneta", "RENTON, Elsa", "TABOY, Rosita",
            "FERNANDEZ, Manuel", "DAVAL, Edilberto Reponte", "LEYSON , Rolando Jr",
            "MACO, Roderick Bensing", "BAYOT, Randy", "CABALLERA, Rolando",
        ])

    def test_all_nine_labelled_values_are_populated(self):
        for row in self.processed():
            for field in ("region_raw", "name_raw", "joint_order_raw", "position_raw",
                          "case_numbers_raw", "aliases_raw", "reward_raw",
                          "offenses_raw", "issuing_court_raw"):
                self.assertTrue(row[field].strip(), field)
            self.assertEqual(row["entity_type"], "Individual")

    def test_geometry_handles_values_above_their_label(self):
        row = self.processed()[1]
        self.assertEqual(row["region_raw"], "4A")
        self.assertEqual(row["primary_name"], "VELASCO, Leo")
        self.assertEqual(row["joint_order_raw"], "14-2012")

    def test_multiline_aliases_and_case_numbers_keep_full_values(self):
        rows = self.processed()
        self.assertEqual(rows[10]["aliases_raw"], "TINA MAGLAYA/ ROSA/OSANG/IPAY/ANA/SAMSI")
        self.assertEqual(rows[6]["case_numbers_raw"], "5332-C (17); 8555 and 8556; 7200-SN")
        self.assertIn("Communist party", rows[9]["position_raw"])
        self.assertTrue(rows[9]["position_raw"].endswith("Panay"))

    def test_court_label_is_removed_whether_separate_or_merged(self):
        rows = self.processed()
        self.assertTrue(rows[1]["issuing_court_raw"].startswith("MTC Candelaria"))
        self.assertTrue(rows[0]["issuing_court_raw"].startswith("RTC 8th"))
        self.assertTrue(rows[3]["issuing_court_raw"].endswith("Agusan del Sur"))
        self.assertTrue(all("ISSUING COURT" not in row["issuing_court_raw"] for row in rows))

    def test_alternate_full_name_is_not_a_second_person(self):
        rows = self.processed()
        self.assertEqual(rows[4]["name_raw"], "GUADIANA, Abadias/GUARDIANA, Abdias")
        self.assertEqual(rows[4]["alternate_name_raw"], "GUARDIANA, Abdias")
        self.assertTrue(all(row["alternate_name_raw"] == "" for i, row in enumerate(rows) if i != 4))

    def test_ocr_errors_and_two_review_flags_are_not_silently_corrected(self):
        rows = self.processed()
        self.assertEqual(rows[5]["aliases_raw"], "Myma Malaya/Emang")
        self.assertIn("isa!ladoud go abeweg pue", rows[5]["offenses_raw"])
        self.assertEqual(rows[16]["region_raw"], "C0")
        self.assertEqual(sum(row["_ocr"]["needs_review"] for row in rows), 2)

    def test_raw_ocr_evidence_is_preserved_and_not_mutated(self):
        before = deepcopy(self.raw)
        rows = self.processed()
        self.assertEqual(self.raw, before)
        for raw, processed in zip(before, rows):
            for key in ("left_column_lines", "right_column_lines", "raw_text", "_ocr"):
                self.assertEqual(processed[key], raw[key])

    def test_generated_ids_are_unique_stable_and_not_source_provided_ids(self):
        rows = self.processed()
        self.assertEqual(len({row["unique_id"] for row in rows}), 18)
        self.assertTrue(all(row["unique_id"].startswith("PNP-SDSPPO-MOST-WANTED-") for row in rows))
        reversed_rows = self.processed(list(reversed(self.raw)))
        self.assertEqual([row["unique_id"] for row in rows],
                         list(reversed([row["unique_id"] for row in reversed_rows])))
        self.assertTrue(all(not row.get("source_record_id") for row in rows))

    def test_region_position_court_and_reward_do_not_invent_other_facts(self):
        row = self.processed()[0]
        self.assertEqual(row["reward_raw"], "10,000,000.00")
        self.assertEqual(row["joint_order_raw"], "17-2019")
        for field in ("currency", "nationality", "gender", "birth_date", "status",
                      "address", "designation_date", "source_record_id"):
            self.assertNotIn(field, row)

    def test_repeated_preprocessing_is_idempotent(self):
        rows = self.processed()
        self.assertEqual(self.processed(rows), rows)

    def test_no_rules_do_not_affect_other_sources(self):
        raw = {"name": "Other Source", "_ocr": {"anything": "preserve"}}
        self.assertEqual(PreProcessingEngine().preprocess([deepcopy(raw)], []), [raw])

    def test_missing_label_is_rejected(self):
        rows = deepcopy(self.raw)
        lines = rows[0]["_ocr"]["fields"]["left_column_lines"]["lines"]
        lines[:] = [line for line in lines if line["text"] != "NAME"]
        with self.assertRaisesRegex(ValueError, "OCR labels"):
            self.processed(rows)

    def test_duplicate_label_is_rejected(self):
        rows = deepcopy(self.raw)
        lines = rows[0]["_ocr"]["fields"]["left_column_lines"]["lines"]
        lines.append(deepcopy(next(line for line in lines if line["text"] == "NAME")))
        with self.assertRaisesRegex(ValueError, "OCR labels"):
            self.processed(rows)

    def test_empty_name_is_rejected_before_id_generation(self):
        rows = deepcopy(self.raw)
        lines = rows[0]["_ocr"]["fields"]["left_column_lines"]["lines"]
        lines[:] = [line for line in lines if line["text"] != "SISON, Jose Maria"]
        with self.assertRaisesRegex(ValueError, "Empty OCR value"):
            self.processed(rows)

    def test_missing_geometry_cannot_fall_back_to_list_stringification(self):
        rows = deepcopy(self.raw)
        del rows[0]["_ocr"]["fields"]["left_column_lines"]
        with self.assertRaisesRegex(ValueError, "Missing OCR column evidence"):
            self.processed(rows)

    def test_generic_handler_uses_configured_labels_for_another_document(self):
        def line(number, text, top):
            return {"line_number": number, "text": text, "score": 1,
                    "box": [[0, top], [100, top], [100, top + 10], [0, top + 10]]}
        raw = {"_ocr": {"fields": {"column": {"lines": [
            line(1, "TITLE Example", 10), line(2, "continuation", 22),
            line(3, "REFERENCE", 40), line(4, "ABC-123", 39),
        ]}}}}
        result = PreProcessingEngine().extract_labelled_ocr_fields(raw, {
            "input_field": "column", "labels": {"TITLE": "title", "REFERENCE": "reference"}})
        self.assertEqual(result["title"], "Example continuation")
        self.assertEqual(result["reference"], "ABC-123")

    def test_all_label_matching_ignores_case_and_preserves_original_evidence(self):
        rows = deepcopy(self.raw)
        for row in rows:
            for evidence in row["_ocr"]["fields"].values():
                for line in evidence["lines"]:
                    for label in ("REGION", "NAME", "JOR NO.", "POSITION", "CC NO.",
                                  "ALIAS", "REWARD", "OFFENSE/S", "ISSUING COURT"):
                        if line["text"] == label or line["text"].startswith(label + " "):
                            line["text"] = line["text"][:len(label)].swapcase() + line["text"][len(label):]
                            break
        before = deepcopy(rows)
        actual = self.processed(rows)
        expected = self.processed()
        for first, second in zip(actual, expected):
            for field in ("primary_name", "aliases_raw", "reward_raw", "issuing_court_raw"):
                self.assertEqual(first[field], second[field])
        self.assertEqual(rows, before)
        self.assertEqual([row["_ocr"] for row in actual], [row["_ocr"] for row in before])

    def test_four_observed_double_scale_court_labels_keep_the_complete_value(self):
        # Actual strings reported by the user after 2x OCR, applied to the saved
        # geometry to reproduce the failure without downloading recognition models.
        examples = [
            (2, "ISSuING CouRT RTC 10th Br 7, Bayugan, Agusan",
             "RTC 10th Br 7, Bayugan, Agusan del Sur"),
            (10, "ISSUING COURTRTC 10th JR, Br 36, Calamba,",
             "RTC 10th JR, Br 36, Calamba, Misamis Occidental (Oct 5, 2006)"),
            (12, "ISSUING COURTRTC 12th Judicial Region, Br 23,",
             "RTC 12th Judicial Region, Br 23, Kidapawan City"),
            (14, "ISSUING COURTRTC 1Oth Judicial Region Br 34,",
             "RTC 1Oth Judicial Region Br 34, Cabadbaran City, Agusan del Norte; RTC 10th Judicial Region Br 29, Surigao City"),
        ]
        for index, text, expected in examples:
            with self.subTest(index=index):
                rows = deepcopy(self.raw)
                lines = rows[index]["_ocr"]["fields"]["right_column_lines"]["lines"]
                marker = next(line for line in lines if line["text"].startswith("ISSUING COURT"))
                if marker["text"] == "ISSUING COURT":
                    values = [line for line in lines if min(p[0] for p in line["box"]) > 310
                              and abs(min(p[1] for p in line["box"]) - min(p[1] for p in marker["box"])) < 3]
                    self.assertEqual(len(values), 1)
                    lines.remove(values[0])
                marker["text"] = text
                before = deepcopy(rows)
                actual = self.processed(rows)[index]
                self.assertEqual(actual["issuing_court_raw"], expected)
                self.assertEqual(actual["_ocr"], before[index]["_ocr"])
                self.assertEqual(rows, before)

    def test_value_starting_with_a_label_is_not_a_label_when_in_value_column(self):
        rows = deepcopy(self.raw)
        lines = rows[0]["_ocr"]["fields"]["right_column_lines"]["lines"]
        alias = next(line for line in lines if line["text"] == "Joma Sison")
        for value in ("ALIASNAME", "ISSUING COURTRTC Example", "ISSUING COURT Example"):
            with self.subTest(value=value):
                alias["text"] = value
                self.assertEqual(self.processed(rows)[0]["aliases_raw"], value)

    def test_joined_label_is_not_guessed_without_column_geometry(self):
        rows = deepcopy(self.raw)
        evidence = rows[1]["_ocr"]["fields"]["right_column_lines"]
        marker = next(line for line in evidence["lines"] if line["text"].startswith("ISSUING COURT"))
        marker["text"] = "ISSUING COURTMTC Candelaria, Quezon"
        del evidence["region"]
        with self.assertRaisesRegex(ValueError, "OCR labels"):
            self.processed(rows)

    def test_approximate_spelling_is_not_silently_treated_as_the_expected_label(self):
        rows = deepcopy(self.raw)
        lines = rows[0]["_ocr"]["fields"]["right_column_lines"]["lines"]
        next(line for line in lines if line["text"] == "ALIAS")["text"] = "ALI4S"
        with self.assertRaisesRegex(ValueError, "OCR labels"):
            self.processed(rows)


if __name__ == "__main__":
    unittest.main()
