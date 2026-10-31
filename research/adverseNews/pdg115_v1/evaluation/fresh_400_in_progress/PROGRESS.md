# Fresh UK_GOV evaluation: unfinished

The user supplied a 500-row full-payload CSV. The source selection excluded
14 previously seen article IDs and eight rows without usable title/body among
the first 422 export rows, leaving 400 frozen blind articles. The 400 span 81
`publisher_name` values. See `selection-manifest.json` for file hashes and
the exact selection counts. `export-500.csv.gz` preserves the supplied CSV;
`blind.jsonl` is the frozen 400-article evaluation set.

`review-partial.jsonl` currently contains **60/400** complete GPT-6 full-text
opinions in packet order. Article 60 is `db:176730`; the next article is
**61, `db:37396`**. These are GPT opinions, not independent human labels.
Positive evidence is an exact title/body substring. Follow
`../../policy/labeling-guide.md`, including the product-confirmed rule that
a UK school/council improvement notice alone is negative unless the supplied
text states a specific qualifying breach or enforcement basis.

Continue by reading full titles and bodies in small batches. Do not inspect
the model's scores for these articles until the 400 reviews are complete and
their file hash is frozen. `code/evaluate_packet.py` deliberately requires
all 400 reviews before scoring. Do not add any of these 400 articles to the
5,044-row training set for this model version.

The source manifest includes `reviewed_count: 60` and records this packet as
`frozen_blind_review_in_progress`. The model has not been evaluated on it.
