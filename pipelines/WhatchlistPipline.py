# pipelines/watchlist_pipeline.py
from pathlib import Path
import sys
import json

import pandas as pd
from datetime import datetime, time


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from transforms.preNormalization import PreNormalizationEngine
from transforms.fieldMapper import load_rules, MappingEngine
from transforms.postNormalization import post_normalize_record
from ingestion.downloader import interface as downloader
from pipelines.whatchlistConfigs import WATCHLIST_CONFIGS
from parsing.xmlParser import XmlParser
from parsing.pdfParser import PdfParser
from parsing.htmlParser import HtmlParser
from parsing.tabularParser import TabularParser
from ingestion.downloader.models import DownloadTask
from transforms.preProcessing import (
    detect_entity_type,
    generate_atc_unique_id,
    extract_name_from_url,
    split_atc_date_and_place_of_birth,
    clean_atc_profile_name_fields,
)
from transforms.enrichmentEngine import EnrichmentEngine


PREPROCESSING_HANDLERS = {
    "detect_entity_type": detect_entity_type,
    "generate_atc_unique_id": generate_atc_unique_id,
    "extract_name_from_url": extract_name_from_url,
    "split_atc_date_and_place_of_birth": split_atc_date_and_place_of_birth,
    "clean_atc_profile_name_fields": clean_atc_profile_name_fields,    
}

from database.inserts import (
     insert_raw_watchlist_file,
     insert_per_raw_unparsed_watchlist_payload,
     insert_staging_watchlist_record_staging,
 )


class WatchlistPipeline:

    def __init__(
        self,
        config,
        downloader,
        pre_normalizer,
        mapper,
        post_normalizer,
    ):
        self.config = config
        self.source_name = config["source_name"]
        self.external_id_path = config["external_id_path"]
        self.downloader = downloader
        self.pre_normalizer = pre_normalizer
        self.mapper = mapper
        self.post_normalizer = post_normalizer
        self.parser = self.get_parser()
        self.enrichment_engine = EnrichmentEngine()

    def get_parser(self):
        file_type = self.config.get("file_type", "").lower()

        if file_type == "xml":
            return XmlParser()

        if file_type == "pdf":
            return PdfParser()
        
        if file_type == "html":
            return HtmlParser()

        if file_type in ["csv", "xlsx", "xls"]:
            return TabularParser()

        raise ValueError(f"Unsupported file type: {file_type}")

    def apply_preprocessing(self, record):
        preprocessing_steps = self.config.get("preprocessing", [])

        for step in preprocessing_steps:
            handler_name = step["handler"]
            handler_config = step.get("config", {})

            handler = PREPROCESSING_HANDLERS.get(handler_name)

            if handler is None:
                raise ValueError(
                    f"Unknown preprocessing handler: {handler_name}"
                )

            record = handler(record, handler_config)

        return record

    def run(self):
        print(f"Starting watchlist pipeline: {self.source_name}")

        download_task = DownloadTask(
            url=self.config["url"],
            file_type=self.config.get("file_type"),
            list_name=self.source_name,
        )

        local_path = self.config.get("local_path")

        if local_path:
            downloaded_file_path = ROOT_DIR / local_path
            print(f"Using local file: {downloaded_file_path}")
        else:
            downloaded_file_path = self.downloader.download(download_task)

        results = insert_raw_watchlist_file(
             source_name=self.source_name,
             url=self.config.get("url"),
             file_path=downloaded_file_path,
             file_type=self.config.get("file_type"),
             downloaded_at=datetime.now(),
             schedule=self.config.get("schedule"),
         )
        
        file_id = results["file_id"]
        source_id = results["source_id"]
        list_type_id = results["list_type_id"]

        print(downloaded_file_path)

        raw_records = self.parser.parse(
            file_path=downloaded_file_path,
            config=self.config,
        )
        raw_records = self.enrichment_engine.enrich_dataset(
            records=raw_records,
            rules=self.config.get("enrichment", [])
        )

        raw_count = 0
        staging_count = 0
        final_records = []
        
        for rule in self.config.get("enrichment", []):
    
            config = rule.get("config")

            if not config:
                continue

            if "profile_dir" in config:
                config["profile_dir"] = str(ROOT_DIR / config["profile_dir"])

            if "images_dir" in config:
                config["images_dir"] = str(ROOT_DIR / config["images_dir"])

        for raw_record in raw_records:
            raw_count += 1
            
            raw_record = self.enrichment_engine.enrich_record(
                        record=raw_record,
                        rules=self.config.get("enrichment", [])
                        )
            
            insert_per_raw_unparsed_watchlist_payload(
                 file_id=file_id,
                 source_name=self.source_name,
                 raw_json=raw_record,
                 created_at=datetime.now(),
             )

            current_record = raw_record
            external_id = current_record.get(self.external_id_path)
            current_record = self.apply_preprocessing(current_record)
            # print("AFTER PREPROCESSING:", current_record)
            current_record = self.pre_normalizer.pre_normalize_record(
                source=self.source_name,
                raw_json=current_record,
            )
            # print("AFTER PRE_NORMALIZER:", current_record)

            current_record = self.mapper.map_record(current_record)
            
            # print("AFTER MAPPING:", current_record)

            current_record = self.post_normalizer.post_normalize_record(
                current_record
            )

            final_records.append(current_record)

            insert_staging_watchlist_record_staging(
                 file_id=file_id,
                 source_id=source_id,
                 list_type_id=list_type_id,
                 #raw_record_id=raw_record_id,
                 #source_name=self.source_name,
                 #final_json=current_record,
                 raw_json=current_record,
                 created_at=datetime.now(),
                 external_id=external_id,
             )

            staging_count += 1

        final_dir = ROOT_DIR / "data" / "final"
        final_dir.mkdir(parents=True, exist_ok=True)

        output_file_path = final_dir / f"{self.source_name}_final.jsonl"

        with open(output_file_path, "w", encoding="utf-8") as f:
            for record in final_records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        print(f"Completed watchlist pipeline: {self.source_name}")
        print(f"Raw records inserted: {raw_count}")
        print(f"Staging records inserted: {staging_count}")
        print(f"Final output saved at: {output_file_path}")


if __name__ == "__main__":
    rules_dir = ROOT_DIR / "data" / "rules"

    prenorm_df = pd.read_excel(rules_dir / "preNormalization.xlsx")
    source_config_df = pd.read_excel(rules_dir / "sourceConfig.xlsx")
    post_rules_df = pd.read_excel(rules_dir / "postNormalization.xlsx")

    config = WATCHLIST_CONFIGS["EU-TRAVEL-BAN"]

    pre_normalizer = PreNormalizationEngine(
        prenormalization_df=prenorm_df,
        source_config_df=source_config_df,
    )

    mapping_rules = load_rules(
        mapping_file=rules_dir / "mapping.xlsx",
        source_name=config["source_name"],
    )

    mapper = MappingEngine(mapping_rules)

    class PostNormalizerAdapter:
        def post_normalize_record(self, record):
            return post_normalize_record(record, post_rules_df)

    post_normalizer = PostNormalizerAdapter()

    pipeline = WatchlistPipeline(
        config=config,
        downloader=downloader,
        pre_normalizer=pre_normalizer,
        mapper=mapper,
        post_normalizer=post_normalizer,
    )

    pipeline.run()