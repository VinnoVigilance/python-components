"""Checks for the OneNews source configuration."""

from pathlib import Path

import yaml

from transforms.preProcessingEngine import PreProcessingEngine


def test_onenews_article_url_and_identity_use_api_fields():
    root = Path(__file__).resolve().parents[4]
    config = yaml.safe_load((root / "config/mediaSources.yaml").read_text(encoding="utf-8"))
    source = config["sources"]["ONENEWS_PH_POLITICS_AND_OTHER_NEWS_FEATURES"]

    assert source["api_config"]["record_id_path"] == "id"
    assert source["identity"]["external_id"]["field"] == "id"
    step = source["preprocessing"][0]
    assert step["handler"] == "build_url_from_template"
    result = PreProcessingEngine().build_url_from_template(
        {"id": 123, "slug": "example-story"}, step["config"]
    )
    assert result["SourceURL"] == "https://www.onenews.ph/articles/example-story"
