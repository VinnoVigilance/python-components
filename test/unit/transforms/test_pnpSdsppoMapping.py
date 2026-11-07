"""Check installed Excel rules against the 18 reviewed source records.

PNP_SDSPPO_RULES_WORKBOOK optionally points to the standalone additions XLSX.
The default tests the actual runtime workbooks after the rows are copied.
No model, browser, network or database is used.
"""

import json
import os
import unittest
from copy import deepcopy
from pathlib import Path

import pandas as pd

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from transforms.fieldMapper import MappingEngine, load_rules
from transforms.postNormalization import PostNormalizationEngine
from transforms.preNormalization import PreNormalizationEngine


ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / "data/rules"
SOURCE = "PNP_SDSPPO_MOST_WANTED"


class TestPnpSdsppoMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone = os.environ.get("PNP_SDSPPO_RULES_WORKBOOK")
        mapping_path = Path(standalone) if standalone else RULES / "mapping.xlsx"
        mapping = pd.read_excel(mapping_path, keep_default_na=False)
        for column in (SOURCE + " TYPE", SOURCE):
            if column not in mapping:
                raise AssertionError("Copy Mapping!F1:G429 as two aligned source columns.")
        cls.mapping = mapping
        sources = pd.read_excel(standalone or RULES / "sourceConfig.xlsx",
                                sheet_name="sourceConfig" if standalone else 0,
                                keep_default_na=False)
        cls.sources = sources[sources["source"] == SOURCE]
        if len(cls.sources) != 1:
            raise AssertionError("Append sourceConfig!A2:C2 exactly once.")
        pre = pd.read_excel(standalone or RULES / "preNormalization.xlsx",
                            sheet_name="preNormalization" if standalone else 0,
                            keep_default_na=False)
        cls.pre_rows = pre[pre["source"] == SOURCE]
        if len(cls.pre_rows) != 1:
            raise AssertionError("Append preNormalization!A2:F2 exactly once.")
        cls.picklists = pd.read_excel(RULES / "pickLists.xlsx", keep_default_na=False)
        if standalone:
            cls.picklists = pd.concat([
                cls.picklists, pd.read_excel(standalone, sheet_name="pickLists", keep_default_na=False),
            ], ignore_index=True)
        cls.raw = json.loads((ROOT / "test/fixtures/watchlist/pnp_sdsppo_most_wanted/preprocessed_records.json").read_text(encoding="utf-8"))
        cls.config = deepcopy(WATCHLIST_CONFIGS[SOURCE])
        cls.pre = PreNormalizationEngine(cls.pre_rows, cls.sources)
        cls.mapper = MappingEngine(load_rules(mapping_path, SOURCE))
        cls.post = PostNormalizationEngine(pd.read_excel(RULES / "postNormalization.xlsx"), cls.config)

    def canonical(self, raw):
        normalized = self.pre.pre_normalize_record(SOURCE, raw)
        return self.post.post_normalize_record(self.mapper.map_record(normalized))

    def test_all_eighteen_names_and_internal_ids_survive_mapping(self):
        result = [self.canonical(raw) for raw in self.raw]
        self.assertEqual(len(result), 18)
        self.assertEqual(len({row["EntityId"] for row in result}), 18)
        for raw, row in zip(self.raw, result):
            self.assertEqual(row["EntityId"], raw["unique_id"])
            self.assertEqual(row["EntityType"], "Individual")
            self.assertEqual(len(row["Names"]), 1)
            self.assertEqual(row["Names"][0]["Name"], raw["primary_name"])
            self.assertEqual(row["Names"][0]["NameType"], "Primary Name")

    def test_source_record_ids_are_empty_and_joint_orders_never_become_person_ids(self):
        for raw in self.raw:
            row = self.canonical(raw)
            self.assertEqual(row["Sources"][0]["SourceRecordId"], "")
            self.assertEqual(row["Identifiers"], [])
        self.assertEqual(self.sources.iloc[0]["External_Id_Path"], "unique_id")
        self.assertEqual(self.sources.iloc[0]["entity_field"], "entity_type")

    def test_source_identity_url_and_ocr_note_are_explicit(self):
        for raw in self.raw:
            source = self.canonical(raw)["Sources"][0]
            self.assertEqual(source["SourceName"], "PNP")
            self.assertEqual(source["ListName"], SOURCE)
            self.assertEqual(source["SourceType"], "Official")
            self.assertEqual(source["DatasetCategory"], "Law Enforcement")
            self.assertEqual(source["SourceURL"], "https://sdsppo.pro13.pnp.gov.ph/?page_id=288")
            self.assertIn("OCR", source["Note"])
            self.assertIn("unverified", source["Note"])

    def test_slash_aliases_split_and_keep_multiword_names(self):
        row = self.canonical(self.raw[1])
        self.assertEqual([a["Alias"] for a in row["Aliases"]], ["Ben", "Vic", "Roy", "Ka Danny"])
        row = self.canonical(self.raw[10])
        self.assertEqual([a["Alias"] for a in row["Aliases"]],
                         ["TINA MAGLAYA", "ROSA", "OSANG", "IPAY", "ANA", "SAMSI"])

    def test_alternate_full_name_remains_one_alias_not_an_extra_person(self):
        row = self.canonical(self.raw[4])
        self.assertEqual([a["Alias"] for a in row["Aliases"]],
                         ["GUARDIANA, Abdias", "Badul", "Abdul", "July"])
        self.assertIn("NAME field", row["Aliases"][0]["Note"])

    def test_empty_alternate_name_does_not_create_a_type_only_alias(self):
        for raw in self.raw:
            row = self.canonical(raw)
            self.assertTrue(all(a["Alias"] for a in row["Aliases"]))
            expected = len(raw["aliases_raw"].split("/")) + bool(raw["alternate_name_raw"])
            self.assertEqual(len(row["Aliases"]), expected)

    def test_ocr_alias_spelling_is_preserved_with_a_specific_note(self):
        row = self.canonical(self.raw[5])
        self.assertEqual([a["Alias"] for a in row["Aliases"]], ["Myma Malaya", "Emang"])
        self.assertTrue(all("unverified" in a["Note"] for a in row["Aliases"]))
        self.assertTrue(all(a["AliasType"] == "AKA" for a in row["Aliases"]))

    def test_seven_additional_fields_preserve_the_source_values(self):
        fields = {
            "Offense": "offenses_raw", "Reward": "reward_raw",
            "Joint Order Number": "joint_order_raw", "Region as Listed": "region_raw",
            "Position as Listed": "position_raw", "Case Reference": "case_numbers_raw",
            "Issuing Court": "issuing_court_raw",
        }
        for raw in self.raw:
            items = self.canonical(raw)["additionalInfo"]
            self.assertEqual(len(items), 7)
            self.assertEqual({item["Type"]: item["Value"] for item in items},
                             {kind: raw[field] for kind, field in fields.items()})

    def test_rewards_do_not_invent_a_currency(self):
        for raw in self.raw:
            reward = next(a["Value"] for a in self.canonical(raw)["additionalInfo"] if a["Type"] == "Reward")
            self.assertEqual(reward, raw["reward_raw"])
            self.assertNotIn("PHP", reward)
            self.assertNotIn("USD", reward)

    def test_region_court_and_position_do_not_become_personal_facts(self):
        for raw in self.raw:
            row = self.canonical(raw)
            for field in ("Countries", "Addresses", "Places", "Occupations", "Dates"):
                self.assertEqual(row[field], [])
            self.assertEqual(row["Gender"], "")
            self.assertEqual(row["DateAdded"], "")
            self.assertEqual(row["DateUpdated"], "")

    def test_no_formal_designation_or_current_wanted_status_is_created(self):
        for raw in self.raw:
            row = self.canonical(raw)
            self.assertEqual(row["Measures"], [])
            self.assertEqual(row["Programs"], [])
            self.assertNotIn("status", row)
            self.assertNotIn("Status", {a["Type"] for a in row["additionalInfo"]})

    def test_shared_poster_is_not_a_personal_photo_attachment(self):
        self.assertTrue(all(self.canonical(raw)["Attachments"] == [] for raw in self.raw))

    def test_name_and_alias_search_enrichment_still_runs(self):
        row = self.canonical(self.raw[1])
        self.assertTrue(row["Names"][0]["Normalized_Name"])
        self.assertTrue(row["Names"][0]["Search_Tokens"])
        self.assertTrue(all(a["Normalized_Alias"] for a in row["Aliases"]))

    def test_mapping_does_not_mutate_raw_fields_or_ocr_evidence(self):
        before = deepcopy(self.raw)
        for raw in self.raw:
            self.canonical(raw)
        self.assertEqual(self.raw, before)

    def test_unrelated_source_does_not_receive_the_alias_rule(self):
        raw = {"entity_type": "Individual", "aliases_raw": "First/Second"}
        self.assertEqual(self.pre.pre_normalize_record("OTHER", raw), raw)

    def test_all_categorical_values_exist_exactly_once_in_picklists(self):
        raw = self.raw[0]
        row = self.canonical(raw)
        values = {
            "EntityType": {"Individual"}, "Names.NameType": {"Primary Name"},
            "Aliases.AliasType": {"AKA"}, "Sources.SourceType": {"Official"},
            "Sources.DatasetCategory": {"Law Enforcement"}, "Sources.SourceName": {"PNP"},
            "Sources.ListName": {SOURCE},
            "additionalInfo.Type": {a["Type"] for a in row["additionalInfo"]},
        }
        for path, entries in values.items():
            for value in entries:
                matches = self.picklists[(self.picklists["Path"] == path) & (self.picklists["Value"] == value)]
                self.assertEqual(len(matches), 1, f"Expected one picklist row: {path} = {value}")
        listing = self.picklists[(self.picklists["Path"] == "Sources.ListName") & (self.picklists["Value"] == SOURCE)].iloc[0]
        self.assertEqual((listing["Parent Path"], listing["Parent Value"]), ("Sources.SourceName", "PNP"))


if __name__ == "__main__":
    unittest.main()
