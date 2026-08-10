# PDG-4 — Cloudflare Protection & Crawling Feasibility (findings)

**Target:** https://atc.gov.ph/individuals/ (ATC designated individuals)
**Date:** 2026-08-10
**Verdict:** ❌ Not feasible to fetch via the automated pipeline by honest means.

## What was tested
Probe code (local, throwaway) lives in the git-ignored
`scripts/pdg4_cloudflare_investigation/`. Each probe was bounded: one request,
explicit timeout, no retry loops, bodies saved to `_out/`.

| Probe | Method | Result |
|-------|--------|--------|
| 1 | Plain `requests` GET | **403**, `cf-mitigated: challenge` — "Just a moment…" |
| 2 | `requests` + full real-Chrome headers | **403**, `cf-mitigated: challenge` — headers don't matter |
| 3 | Alt endpoints (wp-json, sitemaps, feeds) | Only `/robots.txt` = 200. **Everything else 403 challenged** — no unprotected API/feed |
| 4 | Real installed Chrome via Playwright (no stealth) | **Interactive Turnstile challenge** (`cType: 'interactive'`) — automation detected, cannot clear |

## Why it's blocked
The whole origin sits behind a **Cloudflare Managed Challenge**. Any client that
can't run the challenge JS (requests/Scrapy) gets a 403. An automated browser
(Playwright/CDP) is detected and escalated to an **interactive Turnstile
widget** that only a human can clear. There is no un-challenged API, sitemap, or
feed to fall back to.

## Boundary held
No bot-detection evasion was used or recommended: no `cloudscraper`,
`playwright-stealth`, `undetected-chromedriver`, `nodriver`. Solving the
Turnstile programmatically = completing a CAPTCHA = out of scope. robots.txt
allows `User-agent: *` and permits `use=reference`, but forbids `ai-train` and
named AI crawlers — consistent with a gentle, human, reference-only pull.

## Recommendation
- **Do NOT** build an ATC collector in `ingestion/` — it cannot run headless
  and an automated browser is blocked. A browser-driven collector would also be
  heavy/fragile and a poor fit for the `requests`-based pipeline.
- **For production data:** use an official published source (DFA-published
  designated-persons list / UN Consolidated List) if/when needed.
- **For a one-off reference pull:** a human saves the rendered page
  (`Ctrl+S` → HTML only) in a normal browser; the saved HTML is then parsed
  offline. This is human access, not automated crawling.

## Status: investigation complete — no production code added.
