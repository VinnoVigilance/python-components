# OCR sources in the watchlist pipeline

Source settings live in `pipelines/watchlistConfigs.py`, as for other lists.
OCR layouts under `config/ocrLayouts/` describe page geometry and field regions;
they contain no executable code or fixed person names.

## Production path

1. The existing browser collector runs `save_image`.
2. `htmlImageResolver` chooses one image URL from acquired HTML. It reads the
   largest configured srcset candidate rather than selecting a thumbnail.
3. `browserBinaryDownload` fetches image bytes in the same browser session.
   `SaveImageAction` checks format/dimensions and saves the unmodified image,
   page HTML and download manifest with one unique capture filename stem.
4. The existing watchlist pipeline hashes the image bytes. A completed duplicate
   stops before parsing/OCR/Core. Incomplete files use existing resume behavior.
5. For a new image version, parserFactory routes `image` to `OcrParser`.
6. The document reader decodes image frames or renders PDF pages; RapidOcrEngine
   recognizes text; OcrRecordExtractor applies the source layout to make records.
7. Existing preprocessing, Excel mapping, post-normalization and Raw/Core
   processing handle those records.
8. The existing attachment service stores only the HTML and manifest matching
   that capture as list-level evidence. Their hashes do not version the poster.

The URL resolver, image action, OCR parser, reader, recognizer and layout
extractor are runtime components. They are not standalone diagnostic scripts.
Existing PDF table/text parsing remains unchanged unless OCR mode is selected.

## Runtime environment

The current PDEA config selects subprocess OCR because OCR packages are installed
in a separate environment. The main job passes source/settings to
`documentOcrWorker`; it uses the shared reader and writes its private result.
Production code does not import `scripts/ocr`.

```powershell
$env:VV_OCR_PYTHON = Join-Path $env:LOCALAPPDATA "VV\venvs\pdea-ocr\Scripts\python.exe"
```

This setting belongs to each machine/service environment, not a committed
workstation path. `requirements-ocr.txt` describes the separate OCR dependencies.
Subprocess execution is optional for sources whose main environment already has
the OCR packages; the parser's default remains `in_process`.

## Existing developer commands

The existing `scripts/watchlist` commands provide stage-by-stage testing. They
are development tools, not commands called by the main job. No parallel suite
of OCR probe scripts is required.

```powershell
python -m scripts.watchlist.stage_ingest PDEA_MOST_WANTED
python -m scripts.watchlist.stage_extract PDEA_MOST_WANTED
python -m scripts.watchlist.run_pipeline_no_db PDEA_MOST_WANTED --preview --limit 3
```

The local source selector recognizes image extensions and excludes the rendered
HTML/manifest. These commands do not write Raw/Core; acquisition can open a
browser and download files. Project imports still require the normal environment.

## PDEA data contract

- Original poster is the primary artifact; its filename is not a person ID.
- Extract the 16 primary names from the currently reviewed layout.
- Preserve unreliable small print and OCR diagnostics in Raw evidence.
- Ignore crosses without assigning arrest, death or other person status.
- Do not infer dates, gender, nationality, case IDs or personal contacts.
- Generate internal `unique_id` from the name; leave SourceRecordId empty.
- A spelling change can change the fallback ID; identical normalized names
  cannot distinguish different people. Layout changes require review.
- Review scores are diagnostics, not human approval or calibrated accuracy.

## Tests and real job

```powershell
python -m unittest discover -s test/unit/parsing -p "test_subprocessOcr.py" -v
python -m jobs.watchlistPiplineJob --source PDEA_MOST_WANTED
```

Runtime unit tests use a fake recognizer in a real child process and perform no
live OCR/network/DB writes. The real job requires registered database lookups,
working storage and the OCR environment; it writes Raw/Core/storage. Verify its
first successful run and then a second run with the same image for duplicate
handling. Local mocked tests do not replace this live check.

Generated `data/ocr/` files, old probe scripts and per-step instructions are
development artifacts. Keep them outside the release; retain source fixtures
and unit tests that cover the runtime contracts and existing-source behavior.
