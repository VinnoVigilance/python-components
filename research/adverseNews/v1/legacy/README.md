# Original PDG-115 research source archive

This folder preserves the uncommitted source files that were left in the main
checkout after the curated v1 research snapshot was committed. It contains
67 files from the original `research/adverseNews/` source tree and three
PDG-115 notes from `docs/`. The source-relative paths and SHA-256 checksums
are listed in `source-manifest.json`.

These files explain how the exploratory article selection, labeling packets,
model screens, encoder comparison, and UK_GOV follow-up were built. They are
**historical provenance**, not the supported v1 entry points. For current
training, scoring, and evaluation, use `../code/` and read `../README.md`.
Some original scripts expect large local `data/` and `results/` directories
that are not part of this source archive, so copying a script alone does not
make every historical experiment reproducible on a fresh device.

The old local extracts, intermediate models, and encoder feature caches remain
in the original checkout's ignored `research/adverseNews/data/` and
`research/adverseNews/results/` folders. They were retained because they may
save time during later research. They are **not** included in this archive or
uploaded by this commit. The frozen v1 training data, model, and selected
evaluation packets are already tracked under `../`.
