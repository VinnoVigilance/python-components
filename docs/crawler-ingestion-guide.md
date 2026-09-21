# Crawler Ingestion Guide

**Read this before onboarding or debugging a crawler (`download_method: CRAWLER`)
watchlist.** It captures the rules and traps that keep costing us time. Plain
language, concrete examples. Companion to the layering rules in `CLAUDE.md`.

---

## 1. How a crawler list is wired

Three files, one job each:

| File | Its job |
|---|---|
| `pipelines/watchlistConfigs.py` | Declares the list: `source_name`, `list_name`, `url`, `download_method: CRAWLER`, `source_config`, `attachments`, `preprocessing`. |
| `config/watchlistSources/<list>.yaml` | The **scraping recipe**: which rows, which fields, which page, which attachments. |
| `ingestion/crawler/spiders/genericSpider.py` | The **generic** spider. Do not add list-specific code here — drive behaviour from the yaml. |

The service turns config into a typed `CrawlerTask`; the spider takes the Task,
never the raw config (see `CLAUDE.md` §1). Add behaviour by **data in the yaml**,
not new spider branches.

### Changing `genericSpider.py` / `storage.py` — the bar is high, and it matters

These are **reusable, list-agnostic layers**. Onboarding a new list should need
**zero** changes here — a new list is a new yaml + mapping rows, nothing more.
Before editing either file, be ready to answer a reviewer: *why can't the existing
spider do this from the yaml, and why isn't this a mapping/data change?*

- **Fixed boilerplate → mapping `constant`; live page-level content → `page_fields`.**
  If a value is a standing string that never changes and you don't care whether it
  drifts, a `mapping.xlsx` `constant` is simplest and needs no scraping. If you want
  the value pulled **live from the source** (so it stays faithful and updates if the
  site reworries it), use the generic **`page_fields`** yaml section — it is scraped
  **once** from the listing page and merged into every record's `list.*`. Both are
  fine; the wrong move is a **list-specific** branch in the spider — keep any spider
  change generic and yaml-driven (`CLAUDE.md` §1).
- **Per-record values the spider "can't reach"** (listing vs detail page, an
  attribute, a repeating field) are almost always a **selector / yaml** problem,
  not a code problem — see §2.
- **If you genuinely must change the reusable layer**, it is its **own** justified
  commit (`refactor(...)` / `feat(...)`) with a test — never smuggled inside an
  "onboard list X" change. A tweak the onboarded list doesn't even use (e.g.
  tidying a `storage.py` branch) does **not** belong in that list's PR.

### yaml anatomy
```yaml
discovery:            # how to find the rows + the link to each detail page
  row_selector: ".eumwlist .views-row"
  detail_link_selector: ".views-field-view-node .field-content"
  detail_link_attribute: "text"      # "text" reads link text, else an attribute
list_fields:          # scraped from the LISTING page rows
detail_fields:        # scraped from each DETAIL (profile) page
attachments:          # files/links -> record["attachments"]
record_id:            # strategy: url_regex on the detail URL
storage:              # save_listing_page / save_detail_pages
```

---

## 2. The traps (each one cost us a debugging cycle)

### 2a. Read a field from the page where the value is FILLED IN
A value can appear on both the listing and the profile, but differently.
Example: the listing stores a **template** `"Sentenced to {x} years of prison"`
(the site fills `{x}` with JavaScript); the **profile page** already shows the
finished `"Sentenced to 12 years of prison"`. So `state_of_case` must be read
from `detail_fields`, not `list_fields`. **Rule: pick the source page that shows
the real value, not a template or placeholder.**

### 2b. `::text` grabs DIRECT text only — mind nested markup
The spider builds `selector::text`, which returns only the element's **direct**
text nodes. Two Drupal renderings to watch:

- **label-inline** — value is in a **child** `.field__item`:
  ```html
  <div class="...field-reward-amount..."><div class="field-label">Reward payment</div>
    <div class="field__item">5.000</div></div>
  ```
  → selector `.field--name-field-reward-amount .field__item` (the child).
- **label-hidden** — the element **itself** carries `field__item`:
  ```html
  <div class="...field-reward-currency... field__item">€</div>
  ```
  → selector `.field--name-field-reward-currency` (the element itself; a
  `.field__item` descendant selector matches **nothing** here).

Same trap when the text sits inside inner tags:
```html
<div class="...field-european-arrest-warrant..."><span class="flag"></span>
  <span><strong>FAST</strong> Lithuania</span></div>
```
`.field--name-...::text` returns only the whitespace between the spans → **null**.
Target the element that directly holds the text (`... span:last-child`).

### 2c. The spider sees STATIC HTML — no JavaScript
Scrapy downloads the raw server response and does **not** run JS. Anything the
page builds client-side is invisible to the spider:
- photo carousels, thumbnail strips, image overlays,
- and (observed on EU Most Wanted) the "date under the image".

**Always write selectors against the SAVED download file**
(`data/downloads/<SOURCE>/<LIST>/year=…/…_<timestamp>.html`), not the live DOM in
devtools. If a value is only in the live DOM, it is JS-rendered and the current
crawler **cannot** get it — capturing it needs a JS-rendering fetch
(Playwright/Splash), which is an architecture change to raise before starting.

### 2d. Single vs multi-value fields
A Drupal field with one value often renders as the element itself; with many
values it renders a wrapper with several `.field__item` children. If a field can
repeat (phones, photos, aliases), set `multiple: true` and sanity-check both a
single- and multi-value profile.

---

## 3. Attachments

`record["attachments"]` is a list of `{type, url}` (and, where wired, `metadata`).

- **The `type` is named by us, not the site.** It must be a valid
  `Attachments.Type` picklist value (`data/rules/pickLists.xlsx`): currently
  `Photograph, Document, Poster, Thumbnail, News, Profile, Other, Reference`. If
  you need a new one, tell the user the exact row to add — never invent a value
  that isn't in the picklist.
- **The detail/profile page** is emitted automatically. Give its entry
  `role: detail_page` and set `type:` to the picklist value you want
  (e.g. `Profile`). Legacy configs with `type: DETAIL_PAGE` keep working.
- **An image on the detail page** → `selector` + `attribute: src`, `multiple: true`
  (dedups by URL).
- **A link** (poster PDF, external reference) → `selector` + `attribute: href`.
- **A value that only exists on the LISTING** (e.g. a small thumbnail whose URL is
  plain text in a cell) → put it in `list_fields` with `as_attachment: <Type>`;
  the spider turns that list value into an attachment.

There are **two** separate attachment mechanisms — don't confuse them:
1. `record["attachments"]` (this section) → feeds the canonical `Attachments[]`
   via `mapping.xlsx`.
2. `watchlistConfigs["…"]["attachments"]` (`scope`, `attachment_type`,
   `local_path_field`) → stores files as DB attachment records. Its
   `attachment_type` is a DB value, independent of the canonical picklist.

---

## 4. `source_record_id`

- The spider sets it from `record_id` (`url_regex` on the detail URL → the URL
  slug, e.g. `kanys-renaldas`). That slug is also used to **name the saved detail
  HTML file**, so it earns its keep even when overwritten.
- **If the site gives a stable unique id** (a Drupal `nid`, an API id), use it
  directly — do **not** hash an id that is already unique. Point
  `external_id_path` at that raw top-level field, the way OFAC does
  (`"external_id_path": "id"`). No preprocessing needed.
- **`generate_composite_id` always SHA-256-hashes** its joined `fields` (there is
  **no** `hash: false` option — the handler ignores it). Use it only when you must
  build one id out of several fields that have no single stable key; it accepts a
  `prefix`. For a single already-unique field, prefer `external_id_path` above.
- The `_extracted.jsonl` shows the raw record (pre-preprocess); an id written by
  preprocess appears from `_preprocessed.jsonl` onward.

---

## 5. Entity-type routing
`detect_entity_type` defaults every record to **Individual**. An all-Entity or
all-Vessel list maps to nothing until its type is stamped. For a single-type
list, stamp it in preprocessing with `set_constant_field`
(`output_field: entity_type`). Multi-type lists route via `sourceConfig.xlsx` +
an `enum` rule (see `CLAUDE.md` §1).

---

## 6. Downloads, dev harness, data quirks

- **Filenames are timestamped:** `CrawlerStorage` saves the listing as
  `<LIST>_<YYYYMMDD_HHMMSS>.html` (matches the downloader convention). Avoid
  `source_name == list_name` in the config — it makes a doubled
  `X/X/…` directory.
- **Dev stages** (`scripts/watchlist/stage_*.py`, `scripts/watchlist/run_all_no_db.py`)
  run the pipeline DB-free. The real output of `extract` is
  `data/raw/<LIST>_extracted.jsonl`. `meta.json` is a sidecar for chaining
  file-based stages and is **not** written for crawler sources.
- **Validate the extract before building mapping.** Onboarding order:
  crawl → check `_extracted.jsonl` is what you want → *then* add the
  `mapping.xlsx` column, entity-type stamp, and picklist rows.
- **Data quirks:** European decimals (`4,5` = 4.5; `5.000` = 5000) — handle at
  mapping. A `�` in the Windows console is usually a display artifact of a real
  UTF-8 char (e.g. `€`), not corrupted data — verify in code before "fixing" it.

---

## 7. New-crawler checklist
1. Add the config block in `watchlistConfigs.py` (distinct `source_name`).
2. Write the yaml recipe; test selectors against the **saved** HTML.
3. Confirm each field reads from the page that shows the **filled** value (§2a).
4. Attachment types = picklist values; profile page = `role: detail_page` (§3).
5. Decide the external id: point `external_id_path` at the raw stable id field (§4).
6. Stamp `entity_type` if the list isn't all-Individual (§5).
7. Crawl, validate `_extracted.jsonl`, **then** build mapping + picklist rows.
