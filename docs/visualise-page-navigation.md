# /visualise Page & Table Navigation — Complete Reference

This is the full reference for how a `/visualise` request decides which
table/column to filter and which report page to land on, and the complete
current map of every table in this dataset. Keep this up to date whenever a
request lands in the wrong place — the fix should go here (and into the live
keyword library) immediately, not get lost in chat history.

## Step 0: Typo Correction (runs before everything else)

`_correct_typos()` runs on the raw message first, in `/api/visualise`,
before the LLM call or any of this app's own matching sees it. This matters
because the LLM is reasonably typo-tolerant on its own, but this app's OWN
deterministic matching (`_guess_subject_table`, page-keyword lookups) does
plain word comparison and has ZERO typo-tolerance - "delivries" is simply
not "deliveries" to a set-intersection check, no matter how typo-tolerant
the LLM is being.

Uses a real dictionary (`pyspellchecker`), not a character-similarity ratio
- tested and rejected a pure-ratio approach first: `"order"` vs `"border"`
scores HIGHER (0.909) than some genuine typos, so fuzzy-matching arbitrary
words would create false corrections. A dictionary doesn't have that
problem: it already knows "border" is a correctly-spelled word and leaves
it alone, while "delivries" isn't a word at all and gets fixed to
"deliveries".

Guardrails (all required to attempt a correction):
- Word length > 4 chars - protects short acronyms/codes (`CFM`, `MLA`) from
  being "corrected" into an unrelated real word (undictioned "cfm" would
  otherwise get "corrected" to "com").
- Not capitalized - protects proper nouns (airline/company/person names).
- Alphabetic only, and not already a recognized dictionary word.

Verified: `"show delivries to custmer air inida"` → correctly resolves to
`OD_DELIVERIES.Airline_Norm` (previously landed on
`TIRALIS_AIRLINE_FINANCIAL` because "delivries" broke subject-table
detection entirely). `"CFM"` still expands to all 66 real engine variants,
unaffected by the spell-checker.

New dependency: `pyspellchecker` (`pip install pyspellchecker`).

## Hidden-From-Nav Pages (drill-through sub-pages)

Some report pages aren't meant to be jumped to directly - a page reached
only via an in-report drill-through click, with its own built-in "BACK"
button and no entry in the visible tab bar, would look broken/confusing if
the model jumped straight there, since the user never actually drilled in.

**Important: taught keywords alone can't fix a case like that.** If a page's
own TITLE is an obvious keyword match on its own, clearing its taught
keywords doesn't stop the model recommending it. The reliable fix is
removing it from the list of pages the model is even shown:

```bash
curl -X POST http://127.0.0.1:8080/api/pages/hide \
  -H "Content-Type: application/json" \
  -d '{"page": "<exact page title>", "hidden": true}'
```

This is implemented via a `hidden_from_nav` flag on the page's entry in
`page_registry.json` (preserved across the automatic re-sync that runs on
every report load - a plain replace would've silently un-hidden it on
refresh). `_build_filter_system_prompt` filters these out before building
the page list shown to the model. **This does NOT affect the backend's own
tier-2 confirmed-slicer resolution** - if an actual filter condition's only
known slicer genuinely lives on a hidden page, that still correctly lands
there, since tier 2 reads `field_registry`/`page_registry` directly and
never consults the model's page list.

Currently hidden: *(none)*. "BS and PL" was hidden here for a while on the
(incorrect) assumption that it was a drill-through sub-page of "Airline
Financials" - see "Resolved/Corrected 2026-10-28" below for why that was
wrong and it's now unhidden as its own direct destination.

## How Resolution Works (4-tier priority)

1. **Model's own page hint** — lowest priority, only used as a fallback if
   nothing below produced a page. A hint like "Aircraft Registry" can look
   very plausible (the table is literally named
   `AIRCRAFT_REGISTRY_FLEET_DATA_FINAL`) while being flat wrong about where
   the resolved field's slicer actually lives.
2. **Confirmed slicer binding** — if the resolved table/column has a real
   on-screen slicer captured (via the one-time slicer sweep on report load),
   that page wins. This is ground truth.
3. **Table-name-to-page-title fuzzy match** (`_guess_page_for_table`) — used
   when there's no confirmed slicer. Works well when the table name and page
   title share real words (`OD_DELIVERIES` ↔ "Deliveries"). Fails when a
   table's name coincidentally resembles a DIFFERENT page's title than where
   it's actually used (the `TIRALIS_AIRLINE_FINANCIAL` vs "Airline
   Financials" vs "BS and PL" trap — see below).
4. **Taught keywords** (`page_keywords.json`, via `POST /api/pages/keywords`)
   — fed directly into the LLM's prompt as `"PageName (aka: kw1, kw2, ...)"`.
   The fix for cases (3) gets wrong: teach the real association once, used
   for every future request.

## Table Consolidation — Keeping Multi-Condition Requests on One Table

Separately from page selection, the backend also checks whether a request's
condition(s) should all live on the **same table** (`_guess_subject_table` +
the redirect logic in `_validate_one_filter_spec`). This catches the model
picking a technically-valid-but-wrong table for part of a request - e.g.
"how many deliveries made to Air India" is one filter condition (Air India),
but the model has at various points resolved it against
`TIRALIS_AIRLINE_FINANCIAL.AIRLINE` (a column that happens to also be called
"AIRLINE") instead of `OD_DELIVERIES`, purely because nothing else in the
request forced it back on-topic.

**The rule, and why it has one exception:**
- If the message names a table distinctively (e.g. "deliveries" →
  `OD_DELIVERIES`), any condition resolved to a DIFFERENT, unrelated table
  gets redirected onto an equivalent column within the named table instead
  (verified against real data first, never blind).
- **Exception: dimension tables are never redirected away from.** Any table
  named `DIM_*` (see the dimension-table list below) is a shared dimension
  by this schema's convention, DESIGNED to be reused across many subject
  areas via relationships in a star schema. "jurisdiction risk of albania"
  correctly resolving to `DIM_COUNTRY` (not `JURISDICTION_RISK_ANALYSIS`'s
  own denormalized country column) is the *right* answer, not a mistake to
  correct - forcing it back would just lose a working slicer for no
  correctness gain.

This was tightened on 2026-10-26 after table-consolidation was found to be
gated to "only run for multi-condition requests," which left single-
condition requests like the Air India/deliveries case above with zero
protection. It now always runs, with the dimension-table exception doing the
actual protecting (a cleaner, more general rule than counting conditions).

**Attempted and reverted on 2026-10-27**: a categorical condition landing on
a slicer-less column (e.g. `OD_DELIVERIES.Customer Name`, no slicer) stays
correctly redirected to its data for filtering, but the matching on-screen
checkbox never visually highlights, since nothing calls `setSlicerState()`
for a field with no `visual`. A fix was tried that protected any *confirmed*
slicer-sibling redirect from the subject-table revert above (so e.g.
`OD_ORDERS.Customer Name`, which has a real slicer, would stick instead of
reverting back to the slicer-less `OD_DELIVERIES.Customer Name`), plus a
tie-break in `_find_equivalent_column` preferring `DIM_*` tables when
multiple candidates matched equally well.

This made the underlying data-filtering and single-condition cases correct,
but **broke multi-condition page navigation**: when a request like "air
india, boeing, 2025" has conditions that each resolve to a *different*
table with its own slicer (e.g. airline → `DIM_AIRLINE`, manufacturer →
`MARKET_VALUE_LEASE_RATES`), the frontend navigates to whichever condition's
page comes first in the response - which is no longer reliably the
Deliveries page, since both of those tables now resist being pulled back to
the subject table. Landing on the wrong page entirely is worse than a
checkbox not visually highlighting (the data was still filtering correctly
either way), so this was reverted in full - both the slicer-sibling
protection and the `DIM_*` tie-break.

**Still open**: the real fix needs page selection to prioritize the
subject table's own page regardless of which table each individual
condition's slicer happens to live on, rather than reusing per-condition
slicer pages as the navigation target. That's a page-selection change, not
a table-resolution change, and hasn't been attempted yet - don't repeat the
2026-10-27 approach (protecting slicer-sibling redirects) without solving
this first, or the same multi-condition navigation regression will return.

## Complete Table → Page Map

Pages with a **confirmed slicer** (ground truth - ignore any title-guess
that disagrees with this):

| Table | Page | Topic |
|---|---|---|
| `AIRCRAFT_REGISTRY_FLEET_DATA_FINAL` | Aircraft Specifics (also feeds Aircraft Registry, AR_Graphs - same table, multiple pages) | Fleet registry: registration, manufacturer, model, operator, owner, engine |
| `DIM_AIRCRAFT` | Summary | Aircraft dimension (ICAO code, manufacturer) |
| `DIM_AIRLINE` | Summary | Airline name dimension |
| `DIM_DATE` | Summary | Date dimension |
| `DIM_COUNTRY` | Jurisdiction Risk | Country dimension (name, alpha3, region) - **the dimension exception table** |
| `DIM_ENGINE` | Engine Profile | Engine dimension (manufacturer, family, variant) |
| `DIM_REPORTING_PERIOD` | Aviation Finance | Reporting period/year dimension |
| `EMISSIONS_ANALYSIS` | Emissions | Flight hours, flight cycles, CO2/emissions metrics |
| `ENGINE_PROFILE` | Engine Profile | Engine/aircraft type certification data |
| `JURISDICTION_RISK_ANALYSIS` | Jurisdiction Risk | Country-level repossession/legal risk scoring |
| `MARKET_VALUE_LEASE_RATES` | Market Value / Lease Rates | Aircraft valuation, lease rate benchmarking |
| `OD_ORDERS` | Orders | Aircraft order backlog (customer, model, date, manufacturer) |
| `TIRALIS_AIRLINE_FINANCIAL` | **BS and PL** | Airline balance sheet / P&L generator - see "Resolved/Corrected 2026-10-28" below |

Tables with **no confirmed slicer** (page below is a title-guess or inferred
from content inspection, not ground truth - verify before trusting):

| Table | Likely page | Topic |
|---|---|---|
| `OD_DELIVERIES` | Deliveries (title-match only - this page's own controls were never detected as native Power BI slicers) | Aircraft deliveries (customer, manufacturer, date, quantity) |
| `DELIVERY GEO DISTRIBUTION` | Deliveries (probable) | Delivery counts by geography |
| `FIN_A` | Aviation Finance (confirmed by content, no slicer) | "Financier Analysis" - deal financing broken down by financier, H1/H2/year |
| `FACILITY_A` | Aviation Finance (confirmed by content, no slicer) | "Facility Analysis" - financing facility type breakdown |
| `GEO_A` | Aviation Finance (confirmed by content, no slicer) | "Geographic Analysis" - financing deals by geography |
| `BORROWER Transposed` | Aviation Finance (confirmed by content, no slicer) | Deal volume ($ and count) by borrower |
| `MLA CM-CO merged` | Aviation Finance (confirmed by content, no slicer) | Master Lease Agreement deals, Capital Markets vs. Commercial split |
| `AIRCRAFT_REGISTRY_FLEET_DATA_FINAL_COPY` | Unknown - likely an unused duplicate/backup of the main registry table | Same shape as the main registry table |
| `CategoryHelper`, `MetricHelper` | N/A | Internal DAX helper tables, not user-facing data |

**Resolved/Corrected 2026-10-28** (supersedes the "Resolved 2026-10-27" fix
below, which turned out to be backwards): a screenshot on 2026-10-27 showed
"Airline Financials" titled "BALANCE SHEET AND PROFIT & LOSS GENERATOR"
with Airline / Financial Year / Reporting Period slicers, which looked like
confirmation that `TIRALIS_AIRLINE_FINANCIAL`'s three slicer fields
(originally tagged to the hidden "BS and PL" page) were mis-tagged and
needed repointing to "Airline Financials". That fix was applied and shipped.

Live user testing on 2026-10-28 proved this wrong: **"balance sheet" and
"Airline Financials" are two separate pages**, not one. The original
registry data (those three fields pointing at "BS and PL") was correct all
along - it came from the one-time ground-truth slicer sweep, which found
those actual slicer visuals physically on the "BS and PL" page. The
screenshot was misleading: "Airline Financials" is a distinct tab that
merely links/drills into the balance-sheet generator; it isn't the page the
slicers live on.

**Reverted**: `field_registry.json`'s three `TIRALIS_AIRLINE_FINANCIAL`
fields (`AIRLINE`, `FINANCIAL YEAR`, `REPORTING PERIOD`) point back to "BS
and PL"'s page ID. "BS and PL" is **no longer hidden from nav**
(`hidden_from_nav: false`) - it was wrongly marked as a drill-through-only
sub-page under the same mistaken screenshot assumption; it's actually its
own directly-reachable destination. Its taught keywords (balance sheet,
p&l, income statement, financial*, tiralis, etc.) moved from "Airline
Financials" to "BS and PL" to match. "Airline Financials" now has no
confirmed fields and no taught keywords - it's a real, separate page you can
still navigate to by name/title, just with no filter data of its own known
to this app yet.

**Lesson**: a screenshot of report CONTENT (what's visually shown on a tab)
is not proof of which page ID that content's slicer visuals are registered
under - trust the slicer sweep's `field_registry.json` page assignment
(ground truth from walking `report.getPages()`) over a visual/title
impression, and get the user to confirm live navigation behavior before
treating a content screenshot as settling a page-mapping question.

## The Finance-Page Trap (why 4 pages all sound "financial")

This report has **four** pages that casually sound financial. They are NOT
interchangeable - mixing them up was the exact confusion that caused the bug
above, so the distinction matters:

- **Aviation Finance** = deal/loan financing analytics - who's financing
  aircraft deals, which facility, which geography, which borrower, Capital
  Markets vs. Commercial funding. "Who's lending money for aircraft, and
  how." Backed by `FIN_A`, `FACILITY_A`, `GEO_A`, `BORROWER Transposed`,
  `MLA CM-CO merged`, `DIM_REPORTING_PERIOD` - none of these have their own
  slicer; page assignment is by content inspection only.
- **BS and PL** = a specific airline's own balance sheet / P&L statements
  (assets, liabilities, equity, costs, tax, EBIT, etc.). "What does this
  airline's own balance sheet look like." Backed by
  `TIRALIS_AIRLINE_FINANCIAL`, confirmed slicers (`AIRLINE`, `FINANCIAL
  YEAR`, `REPORTING PERIOD`) - this is the correct destination for
  "balance sheet" / "p&l" / "income statement" requests, not "Airline
  Financials" (see "Resolved/Corrected 2026-10-28" above).
- **Airline Financials** = a separate tab in the same report; no confirmed
  fields or taught keywords yet. Reachable by direct navigation/title match
  only until its actual content is investigated and registered.
- **Market Value / Lease Rates** = asset valuation / lease rate benchmarking.

## Current Taught Keywords

Seeded via `POST /api/pages/keywords`, stored in `page_keywords.json`,
injected into every `/visualise` prompt as `"PageName (aka: ...)"`:

| Page | Keywords |
|---|---|
| Aircraft Utilisation | hours, flight hours, flight cycles, cycles, utilisation, utilization |
| Aviation Finance | financier, facility, lender, lending, loan, deal volume, deal financing, financing deals, borrower, capital markets, commercial financing, mla, master lease agreement, geographic financing, aviation financing |
| BS and PL | airline financial statements, airline income statement, airline balance sheet, airline p&l, balance sheet, income statement, p&l, profit and loss, tiralis, finance, financial, financial data, financial information, financial overview, financial summary, financials *(the single default financial destination - moved here from Airline Financials 2026-10-28, see "Resolved/Corrected" above)* |
| Airline Financials | *(none yet - not confused with BS and PL anymore)* |
| Market Value / Lease Rates | asset value, lease rate benchmarking, aircraft valuation, market value, lease rates |

Verified live behavior:
- `"balance sheet for emirates for 2024"` → BS and PL ✓, AIRLINE + FINANCIAL YEAR slicers set to Emirates / FY 2024
- `"go to airline financials"` → Airline Financials ✓ (stays its own distinct page)
- `"show me financial data"` → BS and PL ✓
- `"go to the deal financing page"` → Aviation Finance ✓ (correctly stays separate)
- `"how many deliveries made to air india"` → OD_DELIVERIES / Deliveries page ✓
- `"show delivries to custmer air inida"` (typo'd) → OD_DELIVERIES.Airline_Norm ✓
- `"show jurisdiction risk of albania"` → DIM_COUNTRY (correctly NOT redirected to JURISDICTION_RISK_ANALYSIS) ✓
- `"CFM"` → expands to all 66 real engine variants, unaffected by any of the above ✓

## How to Teach a New Keyword

If a question lands on the wrong page, don't just re-prompt - teach the
association so it's fixed permanently:

```bash
curl -X POST http://127.0.0.1:8080/api/pages/keywords \
  -H "Content-Type: application/json" \
  -d '{"page": "<exact page title>", "keywords": ["term1", "term2"]}'
```

The page title must exactly match a real page (validated server-side against
`page_registry.json`); invalid titles are rejected. Keywords are merged
(deduped), never overwritten. To see the current full library:

```bash
curl http://127.0.0.1:8080/api/pages/keywords
```

## How to Teach a Field-Level Synonym (for slicer-bound columns specifically)

Separately from page keywords, individual slicer-bound columns can carry
their own synonyms too (e.g. `DIM_COUNTRY.Country_Name` → "country",
"nation") - this is what lets the model map a term straight to the ONE
column that actually has a slicer, instead of guessing:

```bash
curl -X POST http://127.0.0.1:8080/api/fields/keywords \
  -H "Content-Type: application/json" \
  -d '{"entries": [{"table": "<exact table>", "column": "<exact column>", "keywords": ["term1", "term2"]}]}'
```
