"""Write a human-friendly index of the prepared paired-label packets."""

import argparse
import json
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("session", type=Path)
args = parser.parse_args()
manifest = json.loads((args.session / "session.json").read_text(encoding="utf-8"))
lines = [
    "# Paired expansion: DeepSeek batch instructions", "",
    f"There are {manifest['articles']} new articles in {len(manifest['batches'])} packets. "
    "Each packet contains up to 10 full articles and the complete frozen policy. "
    "Use the same packet for DeepSeek and GPT; do not share one model’s answers with the other. "
    "Process in order, one packet at a time. Return one JSONL object per article exactly as requested "
    "in the packet. Save each raw DeepSeek reply unchanged as `returns/deepseek/batch-NNN.txt`. "
    "Do not invent labels for missing or unreadable articles; report an incomplete batch for retry.", "",
    "The selection is from training candidates only and is not a prevalence sample. These labels are "
    "proposed silver labels. Agreement between the models is not an accuracy measure or production validation.", "",
    "## Packets", "",
]
lines.extend(f"- [{batch['batch_id']} ({batch['articles']} articles)]({batch['file']})"
             for batch in manifest["batches"])
(args.session / "DEEPSEEK-START-HERE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
