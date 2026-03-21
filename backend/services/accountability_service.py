"""Accountability service — streak calculation, overdue commitments, and consequence chains."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from mentor_agent import _get_permanence

logger = logging.getLogger(__name__)

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

    daily → 1, monthly → 30, everything else (weekly, 3x/week, etc.) → 7.
    """
    freq_lower = str(frequency).lower().strip()
    if freq_lower == "daily":
        return 1
    if freq_lower == "monthly":
        return 30
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


# ── Public API ──


def calculate_streaks(graph) -> list[dict]:
    """Compute current habit streaks from linked daily nodes.

    Returns a list of streak reports sorted by urgency:
    broken first, then at_risk, then on_track.
    """
    results = []

    for habit in graph.get_nodes_by_type("habit"):
        status = str(habit.get("status", "active") or "active").lower()
        if status == "lapsed" or status in _RESOLVED_STATUSES:
            continue

        habit_id = habit["id"]
        frequency = habit.get("frequency") or "weekly"

        # Collect linked daily node dates via any edge type.
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

        # Deduplicate dates and sort most recent first.
        unique_dates = sorted(set(linked_dates), reverse=True)
        today = date.today()

        if not unique_dates:
            results.append({
                "habit_id": habit_id,
                "habit_title": habit.get("title", habit_id),
                "frequency": frequency,
                "status": status,
                "current_streak": 0,
                "last_completed": None,
                "days_since_last": None,
                "streak_status": "broken",
            })
            continue

        last_completed = unique_dates[0]
        days_since_last = (today - last_completed).days
        window = _get_frequency_window(frequency)
        streak = _calculate_streak_count(unique_dates, window)
        status_str = _streak_status(days_since_last, frequency)

        results.append({
            "habit_id": habit_id,
            "habit_title": habit.get("title", habit_id),
            "frequency": frequency,
            "status": status,
            "current_streak": streak,
            "last_completed": last_completed.isoformat(),
            "days_since_last": days_since_last,
            "streak_status": status_str,
        })

    # Sort by urgency: broken → at_risk → on_track.
    _URGENCY = {"broken": 0, "at_risk": 1, "on_track": 2}
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
