# Sprint 8: Passive Habit Inference

> **Epoch:** E6 — Reading the Room (completion)
> **Branch:** `sprint/8`
> **Goal:** Habits stop showing "Broken / No streak" when data exists in daily nodes. Athena infers habit completions from daily content, distinguishes build/break/periodic habits, and displays streaks that match reality.

---

## Overview

E6 is almost complete. State inference, fundamentals monitoring, query-aware retrieval, and relationship intelligence are all shipped. But the accountability view is broken in practice: every habit shows "Broken / No streak / Last: —" because `calculate_streaks()` only counts **explicitly linked** daily nodes via graph edges. In the real vault, daily nodes mention activities in body text ("Full upper body session completed", "1.5hr indoor training", "started 48-hour fast") but are rarely edge-linked to the relevant habit.

Worse, the habit model treats all habits identically. "Nicotine Pouches" (a break habit — goal is days *without*) shows "Broken" which is backwards. "Periodic 48-Hour Fasting" (quarterly) shows "Broken" after a week, which is meaningless. The schema has no way to distinguish these.

This sprint completes E6 by solving Open Problem #6:

1. **Habit kind field** — add `kind: build | break | periodic` to the habit schema. Build habits track consecutive completions. Break habits track days clean. Periodic habits track last done / next due.
2. **Content-based habit inference** — scan daily node body text for activity mentions that match each habit's title/tags/keywords. Infer completions without requiring explicit edges.
3. **Kind-aware streak display** — `calculate_streaks()` returns different metrics per kind: streak count for build, days clean for break, last/next for periodic.
4. **Proactive alert adaptation** — alerts use kind-aware language: "12 days clean" for break habits, "next due in 3 days" for periodic, "streak broken" only for build habits.

**Not in this sprint:** LLM-based inference (too expensive per-message), hybrid confirmation via Telegram ("Looks like you trained today — right?"), or cross-session activity mining from chat history.

---

## What Exists

| Component | State | Relevant to This Sprint |
|-----------|-------|------------------------|
| `accountability_service.py` `calculate_streaks()` | Iterates habits, finds linked dailies via `graph.get_neighbors_with_edges()`, counts dates using `_calculate_streak_count()` | Core function being extended with content inference and kind-awareness |
| `_calculate_streak_count()` | Counts consecutive windows with at least one date occurrence | Reused for build habits; break/periodic need different logic |
| `_streak_status()` | Returns on_track/at_risk/broken based on days since last | Needs kind-aware variants |
| `_get_frequency_window()` | Maps frequency string to window days (1/7/30) | Extended for quarterly (90 days) |
| `_build_proactive_alerts()` in `mentor_agent.py` | Formats broken streaks with "streak broken" language | Needs kind-aware formatting |
| `vault/_meta/schema.md` habit type | `frequency: daily\|weekly\|monthly`, `status: active\|lapsed\|building`, `streak: 0` | Schema gains `kind` field and `quarterly` frequency |
| Vault habit files (13 habits) | Mix of build (strength training, steps), break (nicotine), periodic (fasting) — but no `kind` field | Need migration to add `kind` based on content analysis |
| Vault daily files (14 dailies) | Body text mentions activities by keyword: "gym session", "training", "fast started" | Source data for content-based inference |
| `_FUNDAMENTAL_KEYWORDS` in `accountability_service.py` | Maps keywords to fundamental categories for habit matching | Pattern to follow for habit-specific keyword lists |
| `check_fundamentals()` | Also traverses habit→daily edges for recency detection | Benefits from content inference (same data gap) |
| `GET /api/accountability` endpoint | Returns streaks, overdue, fundamentals | Response shape extended with kind-aware fields |

---

## Architectural Decisions

### 1. Content inference is keyword-based per-habit, not LLM-based

Each habit gets a keyword list derived from its title + tags. When scanning daily node content, a case-insensitive substring match against these keywords determines if the daily "mentions" the habit. This is the same pattern used by `_FUNDAMENTAL_KEYWORDS` and `_habit_matches_fundamental()`.

**Why:** LLM-based inference (calling Claude to classify each daily's content) would cost ~$0.01 per daily per habit per check — prohibitive when run on every chat message. Keyword matching is free, instant, and accurate enough for habits with distinctive vocabulary ("gym", "training", "fast", "pouch", "nicotine"). The vocabulary is derived directly from the habit node itself, so adding a new habit automatically creates its inference keywords. False positives ("I should go to the gym" ≠ "I went to the gym") are acceptable at this stage — the roadmap notes this approach is "Option C — testing this."

### 2. Kind is a schema-level field with a one-time migration, not inferred at runtime

Adding `kind: build | break | periodic` to `schema.md` and migrating existing habits is better than guessing kind at runtime. The kind fundamentally changes how streaks are calculated and displayed — it's a property of the habit definition, not the data.

**Why:** Runtime inference of kind (is this a break habit or a build habit?) requires understanding intent from content, which is ambiguous. "Nicotine Pouches" could be inferred as break from "quitting" status, but that's fragile. The user defining it once is more reliable. Migration is trivial — 13 habit files, each gets one frontmatter line.

### 3. Content inference supplements edge-based linking, doesn't replace it

If a habit has explicit edges to daily nodes, those still count. Content inference adds *additional* daily dates that weren't explicitly linked. The deduplication logic (by date) ensures a daily linked both ways isn't double-counted.

**Why:** Existing edge-based linking is higher confidence (the user or Athena explicitly connected them). Content inference is lower confidence but much higher coverage. Using both gives the best results. Over time, as Athena gets better at proposing links, the edge coverage will grow — but content inference provides the baseline.

### 4. Habit keyword lists are auto-derived from title + tags, with an optional `keywords` frontmatter override

By default, each habit's inference keywords are: words from the title (split on spaces, lowercased, stopwords removed) + all tags. A new optional `keywords` frontmatter field allows the user to add custom keywords that aren't in the title/tags (e.g., "shoulders" for "Strength Training").

**Why:** Auto-derivation covers 80% of cases without any user effort. The override handles edge cases where the daily content uses different vocabulary than the habit title. This is zero-cost for most habits but available when needed.

### 5. Break habit streaks count days since last *mention*, not days since last absence

For break habits (e.g., nicotine), "days clean" = days since the most recent daily that mentions the habit keyword. If the habit hasn't been mentioned in 30 days, that's 30 days clean. A daily that says "had a pouch today" resets to 0.

**Why:** Absence of mention is the best proxy for abstinence in a text-based system. The alternative — requiring daily logs that explicitly confirm "I didn't use nicotine today" — defeats the purpose of passive inference. Open Problem #6 acknowledges this: "missing data ≠ abstinence" is a known limitation. But for a self-hosted personal system, if you wrote a daily node and didn't mention nicotine, you probably didn't use it. This is Option C from the open problem — testing this approach.

---

## Tasks

### Task 1: Schema Update and Habit Migration

**Objective:** Add `kind` field to the habit type in `schema.md`, add `quarterly` to frequency options, and migrate all 13 existing habit files to include `kind`.

**Files to modify:**
- `vault/_meta/schema.md` — update habit type definition
- All 13 files in `vault/Self/Habits/` — add `kind` frontmatter field

**Requirements:**

**Schema change in `schema.md`:**

Update the habit type's frontmatter definition:

```yaml
frequency: daily        # daily | weekly | monthly | quarterly
status: active          # active | lapsed | building | quitting
kind: build             # build | break | periodic
streak: 0              # Optional. Current streak count.
keywords: []           # Optional. Custom inference keywords beyond title/tags.
```

Changes:
- Add `kind: build` with values `build | break | periodic`
- Add `quarterly` to frequency options (supports periodic fasting, quarterly reviews, etc.)
- Add `quitting` to status options (nicotine pouches already uses this — make it official)
- Add `keywords: []` as optional field for custom inference keywords

**Migration mapping for existing habits:**

| Habit | Kind | Rationale |
|-------|------|-----------|
| `strength-training` | build | Consecutive session tracking |
| `12000-steps-daily` | build | Daily activity tracking |
| `5am-wake-target` | build | Consecutive completion |
| `morning-deep-work-block` | build | Consecutive completion |
| `evening-reading-window` | build | Consecutive completion |
| `micro-movement-breaks` | build | Consecutive completion |
| `freedom-daily-routine` | build | Consecutive completion |
| `office-schedule-pattern` | build | Consecutive completion |
| `ralph-delegation-workflow` | build | Consecutive completion |
| `bitcoin-1k-monthly` | periodic | Monthly recurring, not daily streak |
| `periodic-fasting-protocol` | periodic | Quarterly, last-done/next-due |
| `nicotine-pouches-habit` | break | Days clean tracking |
| `social-media-habit` | break | Days clean / reduction tracking |

**Edge cases:**
- Existing habits without `kind` → `calculate_streaks()` defaults to `build` (backward compat)
- `schema_parser.py` already parses any field in YAML blocks — no parser changes needed

**Pattern to follow:** Same frontmatter addition pattern as when `permanence` was discussed (AD#1 in Sprint 2). Add to schema, add to files, let the parser pick it up automatically.

**Acceptance criteria:**
- `schema.md` shows `kind` field with three valid values
- All 13 habit files have appropriate `kind` values
- `quarterly` frequency parses correctly
- Schema endpoint (`GET /api/schema`) reflects the new field
- No existing tests break — `kind` is additive

**Test cases** (manual verification):
- `GET /api/schema` → habit type includes `kind` in frontmatter fields
- Each habit file has valid `kind` value matching the migration table

---

### Task 2: Content-Based Habit Inference

**Objective:** Add a function that scans daily node body text for habit activity mentions, returning inferred completion dates per habit.

**Files to modify:**
- `backend/services/accountability_service.py` — add `infer_habit_completions()` function and supporting helpers

**Requirements:**

```
infer_habit_completions(graph, habit: dict) -> list[date]
```

Takes a single habit node dict and the graph. Returns a list of dates where daily node content suggests this habit was completed.

**Keyword extraction:**

```
_extract_habit_keywords(habit: dict) -> list[str]
```

1. Split title into words, lowercase, remove stopwords (a, an, the, to, of, for, and, or, my, i, is, in, on, at, with)
2. Add all tags (lowercased)
3. Add any items from the `keywords` frontmatter field (if present)
4. Remove duplicates
5. Filter out keywords shorter than 3 characters (too many false matches)
6. Return the list

Example: "Strength Training 3x/week" with tags `["fitness", "training", "strength"]` → keywords: `["strength", "training", "3x/week", "fitness"]`

**Inference logic:**

1. Get all daily nodes from the graph via `graph.get_nodes_by_type("daily")`
2. For each daily node:
   a. Get its `date` field (skip if missing/unparseable)
   b. Get its body content from the node dict (look for `content` or `body` field; if not in the graph node dict, skip)
   c. Lowercase the content
   d. Check if **any** habit keyword appears as a substring in the content
   e. If matched → add this daily's date to the results
3. Return the list of matched dates (not deduplicated — caller handles that)

**Content access:**

The graph stores node attributes from frontmatter parsing. The body content may not be in the graph node dict. If `node.get("content")` or `node.get("body")` returns None, fall back to reading the vault file directly. Add a `vault_root` parameter to `infer_habit_completions()` for file access:

```python
def infer_habit_completions(graph, habit: dict, vault_root: str | None = None) -> list[date]:
```

If `vault_root` is provided and content isn't in the graph, read the markdown file, extract body text (everything after the frontmatter `---` block), and search that.

**Edge cases:**
- Daily with no date → skip
- Daily with no content and no file path → skip
- Habit with no extractable keywords (title is too generic) → return empty list
- Keyword "fast" matching "fast food" → accepted as false positive (keyword length filter removes 1-2 char words, but 3+ char matches are kept). The user can add more specific `keywords` in frontmatter if needed.
- Same daily matching multiple times → deduplicated by caller

**Pattern to follow:** Same graph iteration pattern as `calculate_streaks()`. Same keyword matching approach as `_habit_matches_fundamental()`.

**Acceptance criteria:**
- Daily with body "Full upper body session completed" matches habit "Strength Training" (keyword: "training")
- Daily with body "started 48-hour fast" matches "Periodic 48-Hour Fasting" (keyword: "fasting", "fast")
- Daily with body "Portfolio work continued" does NOT match "Strength Training"
- Keywords derived from title + tags without manual configuration
- Custom `keywords` frontmatter field supplements auto-derived keywords

**Test cases** (`backend/tests/test_habit_inference.py`, new file):
- `test_extract_keywords_from_title_and_tags` — title "Strength Training 3x/week" + tags ["fitness"] → includes "strength", "training", "fitness"
- `test_extract_keywords_removes_stopwords` — "My Morning Routine" → "morning", "routine" (not "my")
- `test_extract_keywords_removes_short` — short words (<3 chars) excluded
- `test_extract_keywords_includes_custom` — frontmatter `keywords: ["shoulders", "arms"]` → included
- `test_infer_matches_daily_content` — daily body "gym session and training" matches habit with keyword "training"
- `test_infer_no_match_unrelated_content` — daily body "portfolio work" does not match "Strength Training"
- `test_infer_case_insensitive` — "TRAINING" in content matches "training" keyword
- `test_infer_skips_dateless_daily` — daily without date field → not in results
- `test_infer_returns_dates` — matched dailies return their date values
- `test_infer_empty_keywords_empty_result` — habit with no derivable keywords → empty list

---

### Task 3: Kind-Aware Streak Calculation

**Objective:** Modify `calculate_streaks()` to use content inference for completion dates and return kind-aware metrics (build streaks, break days-clean, periodic last/next).

**Files to modify:**
- `backend/services/accountability_service.py` — modify `calculate_streaks()`, add `_streak_for_break()` and `_streak_for_periodic()` helpers, update `_get_frequency_window()` and `_streak_status()`

**Requirements:**

**Updated `calculate_streaks()` signature:**

```python
def calculate_streaks(graph, vault_root: str | None = None) -> list[dict]:
```

Add optional `vault_root` for content inference file access. When provided, augment edge-based dates with content-inferred dates.

**Updated flow:**

1. For each active habit, collect dates from two sources:
   a. **Edge-based** (existing): linked daily nodes via `get_neighbors_with_edges()`
   b. **Content-inferred** (new): `infer_habit_completions(graph, habit, vault_root)` — only when `vault_root` is provided
2. Merge and deduplicate dates from both sources
3. Branch on `kind`:

**Build habits** (default, existing behavior):
- Calculate streak using `_calculate_streak_count()` as before
- Return existing fields: `current_streak`, `last_completed`, `days_since_last`, `streak_status`

**Break habits:**
- `days_clean` = days since the **most recent** matched date (the last time the habit was *done*, which is bad for a break habit). If no matches ever → `days_clean = None` (unknown — can't assume they were always clean)
- `streak_status`: if `days_clean` is None → `"unknown"`; if `days_clean >= 30` → `"strong"`; if `days_clean >= 7` → `"on_track"`; if `days_clean >= 1` → `"early"`; if `days_clean == 0` → `"relapsed"`
- Return: `days_clean`, `last_occurrence` (date of most recent mention), `streak_status`

**Periodic habits:**
- Find the most recent completion date
- Compute `next_due` based on frequency: `last_completed + frequency_window_days`
- `days_until_due` = `(next_due - today).days` (negative if overdue)
- `streak_status`: if `days_until_due > 7` → `"on_track"`; if `0 < days_until_due <= 7` → `"upcoming"`; if `days_until_due <= 0` → `"overdue"`; if no completions → `"no_data"`
- Return: `last_completed`, `next_due`, `days_until_due`, `streak_status`

**Updated `_get_frequency_window()`:**

Add quarterly:
```python
if freq_lower == "quarterly":
    return 90
```

**Updated return shape:**

Every streak result now includes a `kind` field:

```python
{
    "habit_id": "strength-training",
    "habit_title": "Strength Training 3x/week",
    "frequency": "3x/week",
    "status": "active",
    "kind": "build",               # NEW
    # Build-specific:
    "current_streak": 3,
    "last_completed": "2026-03-20",
    "days_since_last": 4,
    "streak_status": "at_risk",
    # These are None for build habits:
    "days_clean": None,
    "last_occurrence": None,
    "next_due": None,
    "days_until_due": None,
}
```

Break habit example:
```python
{
    "kind": "break",
    "days_clean": 12,
    "last_occurrence": "2026-03-12",
    "streak_status": "on_track",
    # Build fields are None:
    "current_streak": None,
    ...
}
```

Periodic habit example:
```python
{
    "kind": "periodic",
    "last_completed": "2026-03-16",
    "next_due": "2026-06-14",
    "days_until_due": 82,
    "streak_status": "on_track",
    "current_streak": None,
    "days_clean": None,
    ...
}
```

**Sorting update:**

Sort by urgency across all kinds:
- `relapsed` (break) and `broken` (build) and `overdue` (periodic) → urgency 0
- `at_risk` (build) and `early` (break) and `upcoming` (periodic) → urgency 1
- `on_track` / `strong` / `no_data` / `unknown` → urgency 2

**Edge cases:**
- Habit with no `kind` field → default to `"build"` (backward compat)
- Break habit with zero mentions ever → `days_clean: None`, `streak_status: "unknown"` (don't report as "infinity days clean")
- Periodic habit with `frequency: quarterly` → window = 90 days
- Content inference disabled (`vault_root=None`) → edge-based only (existing behavior preserved)

**Pattern to follow:** Same function signature extension pattern as `check_fundamentals(include_active=False)`. Additive parameter, backward compatible.

**Acceptance criteria:**
- Build habit with daily mentions in content → positive streak count (not 0)
- Break habit "Nicotine Pouches" → shows `days_clean` and appropriate status
- Periodic habit "Periodic 48-Hour Fasting" → shows `last_completed` and `next_due`
- Habits with no `kind` field → default to build behavior
- Edge-linked dates still counted alongside content-inferred dates
- Existing streak calculation tests still pass when `vault_root` is not provided

**Test cases** (add to `backend/tests/test_habit_inference.py`):
- `test_build_streak_with_content_inference` — habit with no edges but daily content mentions → streak > 0
- `test_build_streak_merges_edges_and_content` — edge-linked date + content-inferred date → both counted, no duplicates
- `test_break_habit_days_clean` — break habit mentioned 12 days ago → days_clean: 12
- `test_break_habit_no_mentions_unknown` — break habit never mentioned → days_clean: None, status: unknown
- `test_break_habit_mentioned_today_relapsed` — break habit in today's daily → days_clean: 0, status: relapsed
- `test_periodic_habit_next_due` — quarterly habit done 10 days ago → next_due in ~80 days
- `test_periodic_habit_overdue` — quarterly habit done 100 days ago → days_until_due negative, status: overdue
- `test_periodic_habit_no_completions` — periodic with no data → status: no_data
- `test_default_kind_is_build` — habit without kind field → treated as build
- `test_quarterly_frequency_window` — quarterly → 90-day window
- `test_sorting_across_kinds` — broken build + relapsed break + overdue periodic all sort to top

---

### Task 4: Kind-Aware Proactive Alerts

**Objective:** Update alert formatting in `_build_proactive_alerts()` to use kind-appropriate language for each habit type.

**Files to modify:**
- `backend/mentor_agent.py` — modify streak formatting in `_build_proactive_alerts()`
- `backend/services/chat_service.py` — pass `vault_root` to `calculate_streaks()` in `_build_alerts()`

**Requirements:**

**In `chat_service.py` `_build_alerts()`:**

Pass vault_root to calculate_streaks so content inference is active:

```python
vault_root = current_app.config.get("VAULT_ROOT") or current_app.config.get("vault_root")
streaks = calculate_streaks(self.graph, vault_root=vault_root)
```

**In `mentor_agent.py` `_build_proactive_alerts()`:**

Replace the generic "streak broken" formatting with kind-aware formatting:

**Build habits** (existing behavior, refined):
```
BROKEN STREAKS:
- "Strength Training 3x/week" — last completed 8 days ago, streak broken.
```

**Break habits:**
```
BREAK HABITS:
- "Nicotine Pouches" — 12 days clean. Keep going.
```

Or if relapsed:
```
- "Nicotine Pouches" — mentioned today. Day 0.
```

Or if unknown:
```
- "Nicotine Pouches" — no usage data. Can't determine days clean.
```

**Periodic habits:**
```
PERIODIC:
- "Periodic 48-Hour Fasting" — last completed Mar 16. Next due: Jun 14 (82 days).
```

Or if overdue:
```
- "Periodic 48-Hour Fasting" — last completed Dec 15. Overdue by 10 days.
```

**Alert grouping:**

Split the existing BROKEN STREAKS section into kind-aware sections. Only include sections that have entries:

1. `BROKEN STREAKS:` — build habits with status `broken` or `at_risk`
2. `BREAK HABITS:` — break habits (always show when active, since "12 days clean" is encouraging; only suppress in stressed state)
3. `PERIODIC HABITS:` — periodic habits that are `upcoming` or `overdue`

Break habits with `strong` status (30+ days clean) are NOT shown in alerts (they're stable — no need to surface). Break habits with `unknown` status are shown with softer framing.

**State-aware suppression for break habits:**

Break habits in `early` status (1-7 days clean) are sensitive — don't suppress them even when stressed. The user needs to know they're in the fragile early window. But `strong` (30+ days) can be safely suppressed.

**Edge cases:**
- Mix of kinds in same alert output → each section renders independently
- All habits on_track → no alert sections (same as before)
- Break habit with `unknown` status → include with "no data" framing, don't show as "0 days"

**Pattern to follow:** Same `_build_proactive_alerts()` formatting pattern. Each kind gets its own section with distinct language, same list format.

**Acceptance criteria:**
- Build habits show "streak broken" / "at risk" language (unchanged from before)
- Break habits show "X days clean" instead of "streak broken"
- Periodic habits show "next due: date" or "overdue by X days"
- `strong` break habits (30+ days) not shown in alerts
- `early` break habits (1-7 days) shown even when stressed
- Alert suppression still works for build and periodic habits

**Test cases** (add to `backend/tests/test_mentor_agent.py`):
- `test_proactive_alerts_build_habit_broken` — build habit with broken status → "streak broken" text
- `test_proactive_alerts_break_habit_days_clean` — break habit 12 days clean → "12 days clean" text
- `test_proactive_alerts_break_habit_relapsed` — break habit 0 days → "mentioned today. Day 0."
- `test_proactive_alerts_break_habit_unknown` — break habit unknown → "no usage data"
- `test_proactive_alerts_break_habit_strong_suppressed` — break habit 30+ days → not in output
- `test_proactive_alerts_periodic_upcoming` — periodic due in 5 days → "Next due" text
- `test_proactive_alerts_periodic_overdue` — periodic overdue → "Overdue by X days"
- `test_proactive_alerts_mixed_kinds` — build broken + break early + periodic overdue → all three sections present
- `test_proactive_alerts_break_early_not_suppressed_when_stressed` — stress elevated + break early → break still shown

---

### Task 5: Accountability Endpoint Update and Fundamentals Integration

**Objective:** Update `GET /api/accountability` to return kind-aware streak data and extend content inference to `check_fundamentals()`.

**Files to modify:**
- `backend/routes/graph_routes.py` — pass `vault_root` to service calls, update response shape documentation
- `backend/services/accountability_service.py` — update `check_fundamentals()` to use content inference for daily activity detection

**Requirements:**

**In `graph_routes.py` `/api/accountability` handler:**

Pass `vault_root` to `calculate_streaks()`:

```python
vault_root = current_app.config.get("VAULT_ROOT") or current_app.config.get("vault_root")
streaks = calculate_streaks(g, vault_root=vault_root)
```

The response shape already returns whatever `calculate_streaks()` produces. The new `kind`, `days_clean`, `next_due`, `days_until_due` fields will appear automatically. Update the summary to include kind breakdown:

```json
{
  "summary": {
    "total_habits": 13,
    "on_track": 5,
    "at_risk": 2,
    "broken": 1,
    "overdue_count": 2,
    "oldest_overdue_days": 5,
    "by_kind": {
      "build": {"total": 9, "on_track": 5, "at_risk": 2, "broken": 1},
      "break": {"total": 2, "days_clean_avg": 15},
      "periodic": {"total": 2, "on_track": 1, "overdue": 1}
    }
  }
}
```

**Extend `check_fundamentals()` with content inference:**

`check_fundamentals()` currently finds daily dates only from edge-linked nodes (same limitation as `calculate_streaks()`). Add `vault_root` parameter and use `infer_habit_completions()` to find additional daily dates:

```python
def check_fundamentals(graph, today: date, include_active: bool = False, vault_root: str | None = None) -> list[dict]:
```

In the daily date collection loop (lines 373-383), after collecting edge-linked dates, also run content inference per habit and merge the results. This means fundamentals that were showing "neglected" because of missing edges will now correctly show "active" if daily content mentions the activity.

**Edge cases:**
- `vault_root` not available → edge-based only (backward compat)
- `by_kind` summary with zero break/periodic habits → omit those keys or show zeros

**Pattern to follow:** Same endpoint modification pattern as Sprint 5 Task 5. Additive fields, backward compatible.

**Acceptance criteria:**
- `GET /api/accountability` returns `kind` field on each streak entry
- Break habits show `days_clean` instead of `current_streak`
- Periodic habits show `next_due` and `days_until_due`
- Summary includes `by_kind` breakdown
- `check_fundamentals()` with `vault_root` finds activities from daily content (not just edges)
- Fundamentals that were "neglected" due to missing edges now show "active" when dailies mention the activity

**Test cases** (add to `backend/tests/test_routes.py`):
- `test_accountability_endpoint_includes_kind` — streak entries have `kind` field
- `test_accountability_summary_by_kind` — summary includes `by_kind` breakdown
- `test_accountability_break_habit_days_clean` — break habit entry has `days_clean` field

**Test cases** (add to `backend/tests/test_accountability_service.py`):
- `test_fundamentals_content_inference` — habit with no edges but daily mentions activity → fundamental not flagged as neglected
- `test_fundamentals_without_vault_root_edge_only` — no vault_root → only edge-based (backward compat)

---

## API Response Contracts

### Updated endpoint: `GET /api/accountability`

Existing shape extended with kind-aware fields:

```json
{
  "streaks": [
    {
      "habit_id": "string",
      "habit_title": "string",
      "frequency": "string",
      "status": "string",
      "kind": "build | break | periodic",
      "current_streak": "number | null",
      "last_completed": "string | null",
      "days_since_last": "number | null",
      "streak_status": "on_track | at_risk | broken | relapsed | early | strong | unknown | upcoming | overdue | no_data",
      "days_clean": "number | null",
      "last_occurrence": "string | null",
      "next_due": "string | null",
      "days_until_due": "number | null"
    }
  ],
  "overdue": ["...unchanged..."],
  "summary": {
    "total_habits": "number",
    "on_track": "number",
    "at_risk": "number",
    "broken": "number",
    "overdue_count": "number",
    "oldest_overdue_days": "number",
    "by_kind": {
      "build": {"total": "number", "on_track": "number", "at_risk": "number", "broken": "number"},
      "break": {"total": "number", "days_clean_avg": "number | null"},
      "periodic": {"total": "number", "on_track": "number", "overdue": "number"}
    }
  },
  "fundamentals": ["...unchanged..."],
  "fundamentals_summary": {"...unchanged..."},
  "relationships_summary": {"...unchanged..."}
}
```

### Chat API response: unchanged

No changes to the chat response shape. The system prompt PROACTIVE ALERTS section now uses kind-aware language internally.

---

## Implementation Order

```
Task 1: Schema Update + Habit Migration (independent — vault files only)
  └→ Task 2: Content-Based Habit Inference (depends on Task 1 for keywords field)
      └→ Task 3: Kind-Aware Streak Calculation (depends on Tasks 1 + 2)
          └→ Task 4: Kind-Aware Proactive Alerts (depends on Task 3)
          └→ Task 5: Accountability Endpoint + Fundamentals Integration (depends on Task 3)
```

Task 1 is the foundation. Task 2 depends on Task 1 (needs `keywords` field). Task 3 depends on both. Tasks 4 and 5 depend on Task 3 but are independent of each other.

Recommended sequence: **1 → 2 → 3 → 4 → 5** (schema first, then inference, then kind-aware streaks, then wire into alerts and API).

---

## New Files

| File | Purpose |
|------|---------|
| `backend/tests/test_habit_inference.py` | Content inference + kind-aware streak tests |

**Total new files: 1** (well within the 15-file limit)

---

## Definition of Done

1. All existing tests pass (`python -m pytest` from `backend/`)
2. `npm run build` succeeds in `frontend/`
3. New test file passes with all cases green
4. Habit with no explicit edges but daily content mentioning it → positive streak count (not "Broken")
5. Break habit "Nicotine Pouches" → shows "X days clean", not "Broken"
6. Periodic habit "Periodic 48-Hour Fasting" → shows "last completed / next due", not "Broken"
7. `schema.md` includes `kind: build | break | periodic` and `quarterly` frequency
8. All 13 existing habits migrated with correct `kind` values
9. Proactive alerts use kind-appropriate language
10. `GET /api/accountability` returns `kind`, `days_clean`, `next_due`, `days_until_due` as appropriate
11. `check_fundamentals()` benefits from content inference (fewer false "neglected" reports)
12. No regression in conflict detection, mode classification, permanence scoring, challenge ladder, accountability, state inference, query-aware retrieval, or relationship intelligence
13. No new dependencies added to `requirements.txt`
