"""Integration checks against the real post-normalization handler and engine."""
import pandas as pd
from transforms.postNormalization import generate_media_summary_handler, PostNormalizationEngine, _MEDIA_SUMMARIZER

BODY = "The NBI arrested John Smith on suspicion of fraud. The court later acquitted John Smith after examining the evidence."


def test_pipeline_uses_adaptive_cpu_highlights():
    assert _MEDIA_SUMMARIZER.engine == "hybrid"
    assert _MEDIA_SUMMARIZER.sentences == 0


def test_existing_source_summary_is_preserved():
    entity = {"Content": {"BodyText": BODY, "Summary": "Original source summary"}}
    generate_media_summary_handler(entity, {})
    assert entity["Content"]["Summary"] == "Original source summary"


def test_empty_summary_is_filled_from_body():
    entity = {"Content": {"BodyText": BODY, "Summary": "   "}}
    generate_media_summary_handler(entity, {})
    assert "acquitted John Smith" in entity["Content"]["Summary"]
    assert "arrested John Smith" in entity["Content"]["Summary"]


def test_missing_body_does_not_create_text():
    entity = {"Content": {"BodyText": None, "Summary": ""}}
    generate_media_summary_handler(entity, {})
    assert entity["Content"]["Summary"] == ""


def test_real_engine_normalizes_before_extracting():
    rules = pd.DataFrame([
        {"rule_type": "NORMALIZE_BODY_TEXT", "condition_path": "Content.BodyOriginalValue", "target_path": "Content.BodyText"},
        {"rule_type": "GENERATE_MEDIA_SUMMARY", "condition_path": "Content.BodyText", "target_path": "Content.Summary"},
    ])
    original = {"Content": {"BodyOriginalValue": "<p>"+BODY+"</p>", "Summary": ""}}
    result = PostNormalizationEngine(rules, {"date_order":"DMY"}).post_normalize_record(original)
    assert result["Content"]["BodyText"] == BODY
    assert "acquitted John Smith" in result["Content"]["Summary"]
    assert original["Content"]["Summary"] == ""
