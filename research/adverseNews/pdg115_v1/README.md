# PDG-115 adverse-news research snapshot

**Status:** research-branch snapshot; no push, deployment, or `intelligence/`
integration. The trained model is frozen
for a **review-assisted pilot**. It is not certified to clear or label articles
automatically. The 400-article fresh UK_GOV review is unfinished.

## Plain summary

The fast model is the same LSA 200 + RBF SVM C=3 bundle trained on **5,044**
full articles. `model/model.joblib` is byte-for-byte the already trained
candidate (SHA-256 `eb5829ac409f02d79bd161d973574f82141eaf7bde512da2164987c487df05da`).
The 5,044 rows consist of 5,000 original proposed-label articles with six
product-confirmed policy corrections, plus 18 earlier and 26 later certain
GPT-reviewed additions. Ten uncertain later training opinions were excluded.

The model's newest *completed* UK_GOV check covered 35 unseen articles: it
matched 31/35 GPT opinions, with accuracy **88.6%**, precision **100%**,
recall **50.0%**, and F1 **66.7%**. There were eight GPT-positive articles;
the model found four and missed four. The 117-article UK packet and 150-article
mixed-source packet are retained for diagnostics. Their scores must not be
substituted for this fresh UK result. GPT opinions are not independent human
reference labels. Read `EVIDENCE.md` for the comparison and limitations.

## Folder map

| Folder | Purpose |
| --- | --- |
| `policy/` | Frozen adverse-news label rule v0.2. |
| `training/` | Compressed 5,000-row base, six corrections, two small reviewed addition packets, and assembled 5,044-row training data. |
| `model/` | Frozen 126 MB joblib bundle, original research manifest, and per-article comparison table. Git LFS stores the model. |
| `evaluation/fresh_35/` | Untouched UK_GOV packet and GPT reviews used for the newest completed comparison. |
| `evaluation/diagnostic_117/` | Older random UK_GOV diagnostic; its errors guided training topics. |
| `evaluation/mixed_150/` | Older mixed-source reserved packet. |
| `evaluation/fresh_400_in_progress/` | User's 500-row export, frozen 400-article packet, metadata, and 60/400 saved GPT reviews. **No model scores or training use for these 400.** |
| `code/` | Small self-contained scripts to assemble training data, reproduce the fit, score full articles, and evaluate a completed blind packet. |

The source research tree had over 2 GB of intermediate experiment files and
many one-off scripts. This snapshot keeps the policy, final training inputs,
frozen model, review packets, and the code needed to retrain or score. It
does not duplicate caches, virtual environments, temporary outputs, or old
candidate models. The original local research tree has not been deleted.

## How to use the code

Run from this folder, using a Python environment with `requirements.txt`.
The project `vv-env` currently has the required model runtime versions.

```powershell
# Rebuild only if the assembled training snapshot is absent; refuses overwrite.
python code/assemble_training.py

# Retrain to a NEW output path. Never overwrite the frozen joblib.
python code/fit_model.py --output model/reproduction.joblib

# Score complete title/body articles from JSONL; output is review routing only.
python code/score_articles.py --input articles.jsonl --output scored.jsonl

# Evaluate only a fully labeled packet. A 60-row review of the 400 packet is rejected.
python code/evaluate_packet.py fresh_400_in_progress --output evaluation/fresh_400_in_progress/report.json
```

`score_articles.py` requires `article_id`, `source_name`, `title`, and **full
body** for each input JSON object. `publisher_name` is optional. It emits a
model score and route, always with `review_required=true` and
`auto_clear=false`. Do not load a joblib file from an untrusted source.

## What remains before autonomous production use

1. Finish all 400 full-text GPT reviews and resolve genuine policy ambiguities
   without revealing model predictions. The checkpoint is in
   `evaluation/fresh_400_in_progress/PROGRESS.md`.
2. Compare the frozen model with that untouched packet. Then obtain an
   independent human review of relevant cases, especially negative model
   predictions, and agree on an acceptable adverse-miss rate.
3. If it fails, select **different** articles for new training, retrain, and
   evaluate on another unseen packet. The 400 evaluation articles must not
   become training examples for a model scored against them.

For the senior-engineer explanation: the training file teaches the model;
the evaluation packets check whether that teaching works on new articles;
the model score helps order reviews but does not replace a reviewer.
