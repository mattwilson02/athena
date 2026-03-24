"""Accountability service — streak calculation, overdue commitments, and consequence chains."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

from mentor_agent import _get_permanence

logger = logging.getLogger(__name__)

# Keyword mapping from habit title/tags to fundamental categories.
_FUNDAMENTAL_KEYWORDS: dict[str, list[str]] = {
    "movement": [
        "gym", "training", "workout", "exercise", "run", "running", "walk",
        "cycling", "swim", "climbing", "yoga", "strength", "cardio", "stretch",
        "sport", "physical", "fitness", "steps",
    ],
    "sleep": [
        "sleep", "bed", "bedtime", "wake", "morning routine", "rest", "nap",
        "insomnia", "tired",
    ],
    "nutrition": [
        "meal", "food", "diet", "eat", "cooking", "breakfast", "lunch",
        "dinner", "hydrat", "water intake", "nutrition", "fast", "fasting",
    ],
    "connection": [
        "friend", "family", "partner", "social", "call", "meet",
        "dinner with", "catch up", "hangout", "date night", "relationship",
    ],
    "purpose": [
        "project", "goal", "career", "learn", "study", "create", "build",
        "write", "reading", "skill", "course", "side project", "work on",
    ],
    "financial_stability": [
        "budget", "saving", "expense", "finance", "income",
        "investment", "debt", "rent", "salary",
    ],
}

# Statuses that mean a node is resolved — skip for streak/overdue detection.
_RESOLVED_STATUSES = {
    "completed", "done", "achieved", "overcome",
    "abandoned", "superseded", "cancelled", "attended",
}

# Statuses considered active for overdue detection.
_ACTIVE_STATUSES = {
    "active", "pending", "todo", "in_progress",
    "planning", "blocked", "overdue", "",
}

# Edge types followed when tracing consequence chains.
_CONSEQUENCE_EDGE_TYPES = {"supported_by", "part_of", "blocked_by", "relates_to"}

# Stopwords removed when auto-deriving habit inference keywords from title.
_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "and", "or", "my", "i",
    "is", "in", "on", "at", "with",
}


# ── Helpers ──


def _parse_date(val) -> date | None:
    """Parse a date value from various formats. Returns None if invalid."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        return datetime.fromisoformat(str(val).split("T")[0]).date()
    except (ValueError, TypeError):
        return None


def _get_frequency_window(frequency: str) -> int:
    """Return the window size in days for streak period calculation.

    daily → 1, monthly → 30, quarterly → 90, everything else (weekly, 3x/week, etc.) → 7.
    """
    freq_lower = str(frequency).lower().strip()
    if freq_lower == "daily":
        return 1
    if freq_lower == "monthly":
        return 30
    if freq_lower == "quarterly":
        return 90
    return 7


def _streak_status(days_since_last: int | None, frequency: str) -> str:
    """Return 'on_track', 'at_risk', or 'broken' based on days since last completion."""
    if days_since_last is None:
        return "broken"

    freq_lower = str(frequency).lower().strip()

    if freq_lower == "daily":
        if days_since_last == 0:
            return "on_track"
        if days_since_last == 1:
            return "at_risk"
        return "broken"

    if freq_lower == "monthly":
        if days_since_last <= 24:
            return "on_track"
        if days_since_last <= 30:
            return "at_risk"
        return "broken"

    # weekly and all non-standard frequencies (3x/week, etc.)
    if days_since_last <= 4:
        return "on_track"
    if days_since_last <= 7:
        return "at_risk"
    return "broken"


def _calculate_streak_count(sorted_dates: list[date], window: int) -> int:
    """Count consecutive periods (windows) with at least one occurrence.

    Anchored at the most recent date. Goes backward one window at a time,
    checking whether any date falls within that window.

    sorted_dates: dates most recent first, already deduplicated.
    window: period size in days (1=daily, 7=weekly, 30=monthly).
    """
    if not sorted_dates:
        return 0

    anchor = sorted_dates[0]
    streak = 0
    w = 0

    while True:
        window_end = anchor - timedelta(days=w * window)
        window_start = anchor - timedelta(days=(w + 1) * window - 1)
        if any(window_start <= d <= window_end for d in sorted_dates):
            streak += 1
            w += 1
        else:
            break

    return streak


def _extract_habit_keywords(habit: dict) -> list[str]:
    """Extract inference keywords from a habit's title, tags, and optional keywords field.

    1. Split title into words, lowercase, remove stopwords.
    2. Add all tags (lowercased).
    3. Add any items from the 'keywords' frontmatter field.
    4. Remove duplicates and filter out words shorter than 3 characters.
    """
    keywords: list[str] = []

    # Words from title
    title = (habit.get("title") or "").lower()
    for word in title.split():
        # Strip punctuation-like characters (e.g. "3x/week")
        cleaned = word.strip(",.!?;:")
        if cleaned and cleaned not in _STOPWORDS:
            keywords.append(cleaned)

    # Tags
    tags = habit.get("tags") or []
    for tag in tags:
        keywords.append(str(tag).lower())

    # Custom keywords from frontmatter
    custom = habit.get("keywords") or []
    for kw in custom:
        keywords.append(str(kw).lower())

    # Deduplicate and filter short words
    seen: set[str] = set()
    result: list[str] = []
    for kw in keywords:
        if kw not in seen and len(kw) >= 3:
            seen.add(kw)
            result.append(kw)

    return result


def _get_daily_content(node: dict, vault_root: str | None) -> str:
    """Return body text for a daily node.

    Checks node dict first (content / body field). Falls back to reading
    the markdown file from vault_root when provided and content is absent.
    """
    content = node.get("content") or node.get("body") or ""
    if content:
        return content
    if vault_root is None:
        return ""
    node_id = node.get("id", "")
    if not node_id:
        return ""
    file_path = os.path.join(vault_root, "Life", "Daily", f"{node_id}.md")
    try:
        with open(file_path, encoding="utf-8") as fh:
            raw = fh.read()
        # Extract body text — everything after the closing frontmatter ---
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return parts[2]
        return raw
    except OSError:
        return ""


def infer_habit_completions(
    graph, habit: dict, vault_root: str | None = None
) -> list[date]:
    """Scan daily node body text for mentions of a habit; return matched dates.

    Uses keyword matching (case-insensitive substring) against keywords
    derived from the habit's title, tags, and optional 'keywords' field.

    Returns a list of dates (may contain duplicates — caller deduplicates).
    """
    keywords = _extract_habit_keywords(habit)
    if not keywords:
        return []

    results: list[date] = []

    for daily in graph.get_nodes_by_type("daily"):
        d = _parse_date(daily.get("date"))
        if d is None:
            continue

        content = _get_daily_content(daily, vault_root).lower()
        if not content:
            continue

        if any(kw in content for kw in keywords):
            results.append(d)

    return results


# ── Break / Periodic streak helpers ──


def _streak_for_break(unique_dates: list[date], today: date) -> dict:
    """Compute break-habit metrics: days_clean, last_occurrence, streak_status.

    unique_dates: deduplicated completion (i.e. relapse) dates, most recent first.
    For break habits, a date means the habit was performed — which is bad.
    days_clean = days since the most recent occurrence.
    """
    if not unique_dates:
        return {
            "days_clean": None,
            "last_occurrence": None,
            "streak_status": "unknown",
        }

    last = unique_dates[0]
    days_clean = (today - last).days

    if days_clean == 0:
        status = "relapsed"
    elif days_clean <= 7:
        status = "early"
    elif days_clean < 30:
        status = "on_track"
    else:
        status = "strong"

    return {
        "days_clean": days_clean,
        "last_occurrence": last.isoformat(),
        "streak_status": status,
    }


def _streak_for_periodic(unique_dates: list[date], today: date, frequency: str) -> dict:
    """Compute periodic-habit metrics: last_completed, next_due, days_until_due, streak_status."""
    if not unique_dates:
        return {
            "last_completed": None,
            "next_due": None,
            "days_until_due": None,
            "streak_status": "no_data",
        }

    last = unique_dates[0]
    window = _get_frequency_window(frequency)
    next_due = last + timedelta(days=window)
    days_until_due = (next_due - today).days

    if days_until_due > 7:
        status = "on_track"
    elif days_until_due > 0:
        status = "upcoming"
    else:
        status = "overdue"

    return {
        "last_completed": last.isoformat(),
        "next_due": next_due.isoformat(),
        "days_until_due": days_until_due,
        "streak_status": status,
    }


# ── Public API ──


def calculate_streaks(graph, vault_root: str | None = None) -> list[dict]:
    """Compute current habit streaks from linked daily nodes and content inference.

    Returns a list of streak reports sorted by urgency across all kinds:
    broken/relapsed/overdue first, then at_risk/early/upcoming, then on_track/strong/unknown/no_data.

    vault_root: when provided, augments edge-based dates with content inference.
    """
    results = []
    today = date.today()

    for habit in graph.get_nodes_by_type("habit"):
        status = str(habit.get("status", "active") or "active").lower()
        if status == "lapsed" or status in _RESOLVED_STATUSES:
            continue

        habit_id = habit["id"]
        frequency = habit.get("frequency") or "weekly"
        kind = str(habit.get("kind") or "build").lower()

        # 1. Collect edge-based daily node dates.
        linked_dates: list[date] = []
        seen_ids: set[str] = set()

        for neighbor in graph.get_neighbors_with_edges(habit_id):
            nid = neighbor.get("id")
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            if neighbor.get("type") == "daily":
                d = _parse_date(neighbor.get("date"))
                if d is not None:
                    linked_dates.append(d)

        # 2. Augment with content inference when vault_root provided.
        if vault_root is not None:
            inferred = infer_habit_completions(graph, habit, vault_root)
            linked_dates.extend(inferred)

        # Deduplicate and sort most recent first.
        unique_dates = sorted(set(linked_dates), reverse=True)

        # 3. Build base result dict with all fields (None for non-applicable).
        base = {
            "habit_id": habit_id,
            "habit_title": habit.get("title", habit_id),
            "frequency": frequency,
            "status": status,
            "kind": kind,
            # Build fields
            "current_streak": None,
            "last_completed": None,
            "days_since_last": None,
            # Break fields
            "days_clean": None,
            "last_occurrence": None,
            # Periodic fields (last_completed shared with build)
            "next_due": None,
            "days_until_due": None,
            # Shared
            "streak_status": "broken",
        }

        # 4. Branch on kind.
        if kind == "break":
            metrics = _streak_for_break(unique_dates, today)
            base.update(metrics)

        elif kind == "periodic":
            metrics = _streak_for_periodic(unique_dates, today, frequency)
            base.update(metrics)

        else:
            # Build (default)
            if not unique_dates:
                base["streak_status"] = "broken"
                base["current_streak"] = 0
            else:
                last_completed = unique_dates[0]
                days_since_last = (today - last_completed).days
                window = _get_frequency_window(frequency)
                streak = _calculate_streak_count(unique_dates, window)
                status_str = _streak_status(days_since_last, frequency)
                base.update({
                    "current_streak": streak,
                    "last_completed": last_completed.isoformat(),
                    "days_since_last": days_since_last,
                    "streak_status": status_str,
                })

        results.append(base)

    # Sort by urgency across all kinds:
    # relapsed/broken/overdue → 0, at_risk/early/upcoming → 1, rest → 2
    _URGENCY = {
        "broken": 0, "relapsed": 0, "overdue": 0,
        "at_risk": 1, "early": 1, "upcoming": 1,
        "on_track": 2, "strong": 2, "no_data": 2, "unknown": 2,
    }
    results.sort(key=lambda r: _URGENCY.get(r["streak_status"], 2))
    return results


def find_overdue_commitments(graph, today: date) -> list[dict]:
    """Find all nodes with due/deadline in the past, enriched with consequence chains.

    Returns list sorted by most overdue first (descending days_overdue).
    """
    overdue = []

    for node in graph.get_all_nodes():
        status = str(node.get("status", "") or "").lower()
        if status and status not in _ACTIVE_STATUSES:
            continue

        node_id = node["id"]

        # Find the earliest due/deadline.
        due_date: date | None = None
        for field in ("due", "deadline"):
            val = node.get(field)
            if not val:
                continue
            parsed = _parse_date(val)
            if parsed is not None:
                if due_date is None or parsed < due_date:
                    due_date = parsed

        if due_date is None or due_date >= today:
            continue

        days_overdue = (today - due_date).days

        # Read optional commitment metadata from frontmatter.
        committed_on_raw = node.get("committed_on")
        commitment_context = node.get("commitment_context")

        committed_on: str | None = None
        if committed_on_raw:
            parsed_co = _parse_date(committed_on_raw)
            if parsed_co:
                committed_on = parsed_co.isoformat()

        consequences = trace_consequences(graph, node_id)

        overdue.append({
            "node_id": node_id,
            "title": node.get("title", node_id),
            "type": node.get("type", "unknown"),
            "priority": node.get("priority", "medium"),
            "due": due_date.isoformat(),
            "days_overdue": days_overdue,
            "committed_on": committed_on,
            "commitment_context": commitment_context,
            "consequences": consequences,
        })

    overdue.sort(key=lambda x: x["days_overdue"], reverse=True)
    return overdue


def trace_consequences(graph, node_id: str, max_hops: int = 2) -> list[dict]:
    """BFS from node_id through consequence edge types; return active nodes found.

    Bounded at max_hops. Deduplicates by node_id. Skips resolved nodes.
    Returns results sorted by permanence (identity first, then strategic, tactical).
    """
    consequences: list[dict] = []
    visited: set[str] = {node_id}
    # Queue entries: (current_node_id, hops_taken_so_far)
    queue: list[tuple[str, int]] = [(node_id, 0)]

    while queue:
        current_id, hop = queue.pop(0)
        if hop >= max_hops:
            continue

        for neighbor in graph.get_neighbors_with_edges(current_id):
            nid = neighbor.get("id")
            if nid in visited:
                continue
            visited.add(nid)

            edge_type = neighbor.get("_edge_type", "relates_to")
            if edge_type not in _CONSEQUENCE_EDGE_TYPES:
                continue

            status = str(neighbor.get("status", "") or "").lower()
            if status in _RESOLVED_STATUSES:
                continue

            consequences.append({
                "node_id": nid,
                "title": neighbor.get("title", nid),
                "type": neighbor.get("type", "unknown"),
                "relationship": edge_type,
            })

            queue.append((nid, hop + 1))

    _PERM_ORDER = {"identity": 0, "strategic": 1, "tactical": 2, "ephemeral": 3}
    consequences.sort(
        key=lambda c: _PERM_ORDER.get(_get_permanence(c.get("type", ""))[0], 2)
    )
    return consequences


def _habit_matches_fundamental(habit: dict, keywords: list[str]) -> bool:
    """Return True if the habit's title or tags match any keyword (case-insensitive)."""
    title = (habit.get("title") or "").lower()
    tags = habit.get("tags") or []
    tag_str = " ".join(str(t) for t in tags).lower() if tags else ""
    combined = f"{title} {tag_str}"
    return any(kw in combined for kw in keywords)


def check_fundamentals(
    graph, today: date, include_active: bool = False, vault_root: str | None = None
) -> list[dict]:
    """Check whether core human needs have been neglected for 2+ weeks.

    For each fundamental category, finds habits matching category keywords,
    determines the most recent activity date across all matched habits, and
    reports neglected (>14 days) or no_data (no matching habits) categories.

    Args:
        graph: VaultGraph instance.
        today: Reference date for recency calculations.
        include_active: If True, also return active (≤14 days) fundamentals.
                        Default False — only neglected and no_data are returned.
        vault_root: When provided, augments edge-based dates with content inference
                    for more accurate activity detection.

    Returns:
        List of fundamental reports with keys:
            fundamental, status, days_since_activity, related_habits, message
    """
    all_habits = graph.get_nodes_by_type("habit")
    results: list[dict] = []

    for fundamental, keywords in _FUNDAMENTAL_KEYWORDS.items():
        # Find all habits matching this fundamental (including lapsed ones)
        matched_habits = [h for h in all_habits if _habit_matches_fundamental(h, keywords)]

        if not matched_habits:
            entry: dict = {
                "fundamental": fundamental,
                "status": "no_data",
                "days_since_activity": None,
                "related_habits": [],
                "message": f"No {fundamental.replace('_', ' ')} habits tracked.",
            }
            results.append(entry)
            continue

        related_habit_ids = [h["id"] for h in matched_habits]

        # Collect all daily dates from linked daily nodes across all matched habits.
        all_daily_dates: list[date] = []
        seen_ids: set[str] = set()

        for habit in matched_habits:
            habit_id = habit["id"]
            for neighbor in graph.get_neighbors_with_edges(habit_id):
                nid = neighbor.get("id")
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)
                if neighbor.get("type") == "daily":
                    d = _parse_date(neighbor.get("date"))
                    if d is not None:
                        all_daily_dates.append(d)

        # Augment with content inference when vault_root provided.
        if vault_root is not None:
            for habit in matched_habits:
                inferred = infer_habit_completions(graph, habit, vault_root)
                all_daily_dates.extend(inferred)

        if not all_daily_dates:
            # Habits exist but no dailies recorded — treat as neglected
            entry = {
                "fundamental": fundamental,
                "status": "neglected",
                "days_since_activity": None,
                "related_habits": related_habit_ids,
                "message": (
                    f"No {fundamental.replace('_', ' ')}-related activity recorded. "
                    f"Related habits: {', '.join(h.get('title', hid) for h, hid in zip(matched_habits, related_habit_ids))}."
                ),
            }
            results.append(entry)
            continue

        most_recent = max(all_daily_dates)
        days_since = (today - most_recent).days

        if days_since > 14:
            status = "neglected"
            # Build habit names for message
            habit_names = [h.get("title", h["id"]) for h in matched_habits]
            last_str = most_recent.strftime("%b %-d")
            message = (
                f"No {fundamental.replace('_', ' ')}-related activity in {days_since} days. "
                f"Related habits: {', '.join(habit_names)} (last: {last_str})."
            )
            entry = {
                "fundamental": fundamental,
                "status": "neglected",
                "days_since_activity": days_since,
                "related_habits": related_habit_ids,
                "message": message,
            }
            results.append(entry)
        else:
            if include_active:
                entry = {
                    "fundamental": fundamental,
                    "status": "active",
                    "days_since_activity": days_since,
                    "related_habits": related_habit_ids,
                    "message": None,
                }
                results.append(entry)

    return results
