"""Media normalization prepares body text and preserves source summaries."""

import pandas as pd
import pytest

from transforms.postNormalization import PostNormalizationEngine


@pytest.mark.parametrize("source_summary", [None, "", "Original source summary"])
@pytest.mark.parametrize("legacy_rule_present", [False, True])
def test_normalization_leaves_summary_for_post_processing(source_summary, legacy_rule_present):
    rules = [{
        "rule_type": "NORMALIZE_BODY_TEXT",
        "condition_path": "Content.BodyOriginalValue",
        "target_path": "Content.BodyText",
    }]
    if legacy_rule_present:
        rules.append({
            "rule_type": "GENERATE_MEDIA_SUMMARY",
            "condition_path": "Content.BodyText",
            "target_path": "Content.Summary",
        })

    body = "The regulator announced an enforcement action. The company issued a response."
    content = {"BodyOriginalValue": f"<p>{body}</p>"}
    if source_summary is not None:
        content["Summary"] = source_summary

    engine = PostNormalizationEngine(pd.DataFrame(rules), {"date_order": "DMY"})

    result = engine.post_normalize_record({"Content": content})["Content"]

    assert result["BodyText"] == body
    if source_summary is None:
        assert "Summary" not in result
    else:
        assert result["Summary"] == source_summary
