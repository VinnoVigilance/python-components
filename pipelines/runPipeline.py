"""
Run the watchlist pipeline for one or more sources by name.

The pipeline's own __main__ is hardcoded to a single source; this is a thin
runner so you can regenerate specific lists, e.g. to produce clean _final.jsonl
files (no merge-conflict markers) after a mapping change.

Usage:
    ./vv-env/Scripts/python.exe -m pipelines.runPipeline OFAC-SDN OFAC-NON-SDN UKSL
    ./vv-env/Scripts/python.exe -m pipelines.runPipeline --all
"""

import sys
import time
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from transforms.preNormalization import PreNormalizationEngine
from transforms.fieldMapper import load_rules, MappingEngine
from transforms.postNormalization import PostNormalizationEngine
from ingestion.downloader import interface as downloader
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from pipelines.watchlistPipline import WatchlistPipeline


def run_source(name, prenorm_df, source_config_df, post_rules_df, rules_dir):
    if name not in WATCHLIST_CONFIGS:
        print(f"[SKIP] unknown source: {name}")
        return False

    config = WATCHLIST_CONFIGS[name]
    list_name = config.get("list_name", config["source_name"])

    pre_normalizer = PreNormalizationEngine(
        prenormalization_df=prenorm_df,
        source_config_df=source_config_df,
    )
    mapping_rules = load_rules(
        mapping_file=rules_dir / "mapping.xlsx",
        source_name=list_name,
    )
    mapper = MappingEngine(mapping_rules)
    post_normalizer = PostNormalizationEngine(post_rules_df, config=config)

    pipeline = WatchlistPipeline(
        config=config,
        downloader=downloader,
        pre_normalizer=pre_normalizer,
        mapper=mapper,
        post_normalizer=post_normalizer,
    )
    pipeline.run()
    return True


def main(argv):
    if not argv:
        print(__doc__)
        return

    rules_dir = ROOT_DIR / "data" / "rules"
    prenorm_df = pd.read_excel(rules_dir / "preNormalization.xlsx")
    source_config_df = pd.read_excel(rules_dir / "sourceConfig.xlsx")
    post_rules_df = pd.read_excel(rules_dir / "postNormalization.xlsx")

    if argv == ["--all"]:
        names = list(WATCHLIST_CONFIGS)
    else:
        names = argv

    for name in names:
        start = time.perf_counter()
        ok = run_source(name, prenorm_df, source_config_df, post_rules_df, rules_dir)
        if ok:
            print(f"  -> {name} done in {time.perf_counter() - start:.1f}s\n")


if __name__ == "__main__":
    main(sys.argv[1:])
