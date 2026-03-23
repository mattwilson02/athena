"""Planning service — daily briefing compilation, plan-reality comparison, and pattern detection."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# Category keyword mapping for plan item drop-rate analysis.
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "exercise": [
        "gym", "run", "running", "workout", "exercise", "yoga", "training",
        "cycling", "walk", "swim", "sport", "strength", "cardio",
    ],
    "reading": ["read", "reading", "book", "article", "pages"],
    "work": [
        "work", "code", "coding", "dev", "develop", "project", "pr",
        "review", "meeting", "email", "write", "draft",
    ],
    "meditation": ["meditat", "mindfulness", "breathe", "breathing"],
    "errands": ["grocery", "shopping", "errand", "chore", "dentist", "call"],
    "social": ["meet", "dinner", "coffee", "friend", "family", "catch up"],
}

_DONE_STATUSES = {"done", "completed", "attended"}
_ACTIVE_TASK_STATUSES = {"todo", "in-progress", "in_progress", "active", "pending", "upcoming"}


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


def _extract_time(val) -> str | None:
    """Extract HH:MM time string from a datetime value (e.g. '2026-03-23T14:00')."""
    if val is None:
        return None
    s = str(val)
    if "T" in s:
        time_part = s.split("T")[1]
        if ":" in time_part:
            parts = time_part.split(":")
            return f"{parts[0]}:{parts[1]}"
    return None


def _is_habit_due_today(streak_data: dict) -> bool:
    """Determine whether a habit should be surfaced as due today.

    Rules:
    - daily → always due
    - weekly → due if days_since_last >= 5 (2-day buffer before breaking)
    - 3x/week or similar non-standard → due if days_since_last >= 2
    - monthly → due if days_since_last >= 25
    - No previous completion (days_since_last is None) → always due
    """
    frequency = str(streak_data.get("frequency") or "weekly").lower().strip()
    days_since = streak_data.get("days_since_last")

    if frequency == "daily":
        return True

    if frequency == "monthly":
        return days_since is None or days_since >= 25

    # Detect Nx/week patterns (e.g. "3x/week", "4x/week")
    if "/" in frequency or ("x" in frequency and "week" in frequency):
        return days_since is None or days_since >= 2

    # Default: weekly
    return days_since is None or days_since >= 5


def _priority_key(priority: str) -> int:
    """Sort key for priority: high=0, medium=1, low=2."""
    return {"high": 0, "medium": 1, "low": 2}.get(str(priority).lower(), 1)


def _find_daily_node(graph, target_date: date) -> dict | None:
    """Find the daily node for a given date.

    If multiple daily nodes share the same date (shouldn't happen but defensive),
    returns the one with the most content.
    """
    candidates = []
    for node in graph.get_nodes_by_type("daily"):
        if _parse_date(node.get("date")) == target_date:
            candidates.append(node)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Multiple — pick the one with most content (title + content + planned)
    return max(
        candidates,
        key=lambda n: (
            len(str(n.get("content") or ""))
            + len(str(n.get("planned") or ""))
        ),
    )


def _check_item_completed(graph, daily_node: dict, item: dict) -> tuple[bool, str | None]:
    """Check whether a planned item is complete and return (completed, source).

    Priority:
    1. plan_marked — item.completed is truthy in frontmatter
    2. status_change — linked_node exists and has a done status
    3. daily_linked — daily node has a direct edge to linked_node
    """
    if item.get("completed"):
        return True, "plan_marked"

    linked_id = item.get("linked_node")
    if linked_id:
        linked = graph.get_node(linked_id)
        if linked and str(linked.get("status") or "").lower() in _DONE_STATUSES:
            return True, "status_change"

    if linked_id:
        daily_id = daily_node["id"]
        for neighbor in graph.get_neighbors_with_edges(daily_id):
            if neighbor.get("id") == linked_id:
                return True, "daily_linked"

    return False, None


def _categorize(description: str) -> str:
    """Map a plan item description to a category via keyword matching."""
    lower = description.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return "miscellaneous"


# ── Public API ──


def compile_briefing(graph, accountability_data: dict, today: date) -> dict:
    """Compile a structured daily briefing from existing graph data.

    Args:
        graph: VaultGraph instance.
        accountability_data: Dict with keys "streaks", "overdue", "fundamentals"
            (outputs of calculate_streaks, find_overdue_commitments, check_fundamentals).
        today: The date the briefing is for.

    Returns:
        Structured dict with events, due_tasks, habit_targets, active_plan,
        overdue_summary, fundamentals_status, yesterday_review.
    """
    streaks = accountability_data.get("streaks") or []
    overdue_list = accountability_data.get("overdue") or []
    fundamentals = accountability_data.get("fundamentals") or []

    # 1. Events for today
    events: list[dict] = []
    for node in graph.get_nodes_by_type("event"):
        if _parse_date(node.get("date")) != today:
            continue
        events.append({
            "node_id": node["id"],
            "title": node.get("title", node["id"]),
            "time": _extract_time(node.get("date")),
            "location": node.get("location"),
            "people": node.get("people") or [],
            "status": node.get("status") or "upcoming",
        })
    # Sort by time (timed events first, then untimed)
    events.sort(key=lambda e: (e["time"] is None, e["time"] or ""))

    # 2. Due tasks (due today or overdue, not resolved)
    due_tasks: list[dict] = []
    seen_task_ids: set[str] = set()

    for node in graph.get_nodes_by_type("task"):
        status = str(node.get("status") or "").lower()
        if status in {"done", "completed", "cancelled", "abandoned"}:
            continue
        due = _parse_date(node.get("due"))
        if due is None or due > today:
            continue
        nid = node["id"]
        seen_task_ids.add(nid)
        due_tasks.append({
            "node_id": nid,
            "title": node.get("title", nid),
            "priority": node.get("priority") or "medium",
            "project": node.get("project"),
            "days_overdue": (today - due).days,
        })

    # Also surface overdue task-type nodes not already included
    for item in overdue_list:
        if item["node_id"] not in seen_task_ids and item.get("type") == "task":
            seen_task_ids.add(item["node_id"])
            due_tasks.append({
                "node_id": item["node_id"],
                "title": item.get("title", item["node_id"]),
                "priority": item.get("priority") or "medium",
                "project": None,
                "days_overdue": item.get("days_overdue", 0),
            })

    # Sort: priority ascending (high first), then days_overdue descending
    due_tasks.sort(key=lambda t: (_priority_key(t["priority"]), -t["days_overdue"]))

    # 3. Habit targets due today
    habit_targets: list[dict] = []
    for streak in streaks:
        if not _is_habit_due_today(streak):
            continue
        habit_targets.append({
            "habit_id": streak["habit_id"],
            "title": streak.get("habit_title", streak["habit_id"]),
            "frequency": streak.get("frequency") or "weekly",
            "streak_status": streak.get("streak_status") or "on_track",
            "current_streak": streak.get("current_streak") or 0,
            "last_completed": streak.get("last_completed"),
            "due_today": True,
        })
    _URGENCY = {"broken": 0, "at_risk": 1, "on_track": 2}
    habit_targets.sort(
        key=lambda h: (_URGENCY.get(h["streak_status"], 2), -h["current_streak"])
    )

    # 4. Active plan from today's daily node
    active_plan = None
    today_daily = _find_daily_node(graph, today)
    if today_daily is not None:
        planned_raw = today_daily.get("planned")
        if planned_raw and isinstance(planned_raw, list):
            planned_items = []
            for item in planned_raw:
                if not isinstance(item, dict):
                    continue
                completed, _ = _check_item_completed(graph, today_daily, item)
                planned_items.append({
                    "description": item.get("description", ""),
                    "linked_node": item.get("linked_node"),
                    "completed": completed,
                })
            total = len(planned_items)
            done_count = sum(1 for p in planned_items if p["completed"])
            active_plan = {
                "daily_id": today_daily["id"],
                "planned": planned_items,
                "completion_rate": done_count / total if total > 0 else 0.0,
            }

    # 5. Overdue summary (condensed top-3)
    overdue_summary = {
        "count": len(overdue_list),
        "top_items": [
            {
                "node_id": item["node_id"],
                "title": item.get("title", item["node_id"]),
                "days_overdue": item.get("days_overdue", 0),
                "priority": item.get("priority") or "medium",
            }
            for item in overdue_list[:3]
        ],
    }

    # 6. Fundamentals status
    neglected_funds = [f["fundamental"] for f in fundamentals if f["status"] == "neglected"]
    at_risk_funds = [
        f["fundamental"] for f in fundamentals
        if f["status"] == "active"
        and isinstance(f.get("days_since_activity"), int)
        and f["days_since_activity"] >= 10
    ]
    fundamentals_status = {
        "neglected": neglected_funds,
        "at_risk": at_risk_funds,
    }

    # 7. Yesterday's review
    yesterday_review = None
    yesterday = today - timedelta(days=1)
    yesterday_daily = _find_daily_node(graph, yesterday)
    if yesterday_daily is not None:
        y_planned = yesterday_daily.get("planned")
        if y_planned and isinstance(y_planned, list):
            total = len(y_planned)
            done_count = sum(
                1 for item in y_planned
                if isinstance(item, dict) and item.get("completed")
            )
            missed = [
                item.get("description", "")
                for item in y_planned
                if isinstance(item, dict) and not item.get("completed")
            ]
            yesterday_review = {
                "planned_count": total,
                "completed_count": done_count,
                "completion_rate": done_count / total if total > 0 else 0.0,
                "missed": missed,
            }

    return {
        "date": today.isoformat(),
        "day_of_week": today.strftime("%A"),
        "events": events,
        "due_tasks": due_tasks,
        "habit_targets": habit_targets,
        "active_plan": active_plan,
        "overdue_summary": overdue_summary,
        "fundamentals_status": fundamentals_status,
        "yesterday_review": yesterday_review,
    }


def compare_plan_reality(graph, daily_node_id: str) -> dict | None:
    """Compare the stated plan in a daily node against actual outcomes.

    Returns structured comparison dict, or None if the daily node has no plan.

    Completion sources (priority order):
    1. plan_marked  — item.completed is True in frontmatter
    2. status_change — linked_node exists and has done/completed/attended status
    3. daily_linked  — daily has a direct edge to linked_node
    """
    daily = graph.get_node(daily_node_id)
    if daily is None:
        return None

    planned_raw = daily.get("planned")
    if not planned_raw or not isinstance(planned_raw, list):
        return None

    daily_date = _parse_date(daily.get("date"))

    items: list[dict] = []
    for item in planned_raw:
        if not isinstance(item, dict):
            continue
        completed, source = _check_item_completed(graph, daily, item)
        items.append({
            "description": item.get("description", ""),
            "linked_node": item.get("linked_node"),
            "planned": True,
            "completed": completed,
            "source": source,
        })

    planned_count = len(items)
    completed_count = sum(1 for i in items if i["completed"])
    completion_rate = completed_count / planned_count if planned_count > 0 else 0.0

    # Find unplanned completions: nodes completed on the plan date not in the plan
    planned_ids = {i["linked_node"] for i in items if i.get("linked_node")}
    unplanned: list[dict] = []

    for node in graph.get_all_nodes():
        nid = node["id"]
        if nid == daily_node_id or nid in planned_ids:
            continue
        status = str(node.get("status") or "").lower()
        if status not in _DONE_STATUSES:
            continue

        node_type = node.get("type", "")
        # For events: match by event date
        if node_type == "event":
            if daily_date is not None and _parse_date(node.get("date")) == daily_date:
                unplanned.append({
                    "node_id": nid,
                    "title": node.get("title", nid),
                    "type": node_type,
                    "source": "status_change",
                })
        # For other types: match by updated date
        elif daily_date is not None and _parse_date(node.get("updated")) == daily_date:
            unplanned.append({
                "node_id": nid,
                "title": node.get("title", nid),
                "type": node_type,
                "source": "status_change",
            })

    return {
        "daily_id": daily_node_id,
        "date": daily.get("date", ""),
        "planned_count": planned_count,
        "completed_count": completed_count,
        "completion_rate": completion_rate,
        "items": items,
        "unplanned_completions": unplanned,
    }


def analyze_plan_patterns(graph, lookback_days: int = 14) -> dict:
    """Analyze plan completion patterns over the last N days.

    Reads daily nodes in the lookback window that have plan data, runs
    compare_plan_reality() for each, and aggregates patterns.

    Returns a dict with aggregated stats and an optional planning_insight string
    (generated only when >= 5 days of plan data are available).
    """
    _default: dict = {
        "days_with_plans": 0,
        "avg_planned_items": 0,
        "avg_completion_rate": 0.0,
        "completion_by_day_of_week": {},
        "most_dropped_categories": [],
        "overcommit_days": 0,
        "avg_unplanned_completions": 0.0,
        "planning_insight": None,
    }

    today = date.today()
    cutoff = today - timedelta(days=lookback_days)

    # Collect daily nodes with plan data inside the lookback window
    plan_rows: list[dict] = []
    for node in graph.get_nodes_by_type("daily"):
        node_date = _parse_date(node.get("date"))
        if node_date is None or node_date < cutoff or node_date > today:
            continue
        if not node.get("planned") or not isinstance(node.get("planned"), list):
            continue
        comparison = compare_plan_reality(graph, node["id"])
        if comparison is None:
            continue
        plan_rows.append({
            "date": node_date,
            "day_of_week": node_date.strftime("%A"),
            "planned_count": comparison["planned_count"],
            "completed_count": comparison["completed_count"],
            "completion_rate": comparison["completion_rate"],
            "items": comparison["items"],
            "unplanned_count": len(comparison["unplanned_completions"]),
        })

    if not plan_rows:
        return _default

    n = len(plan_rows)
    avg_planned = sum(r["planned_count"] for r in plan_rows) / n
    avg_completion = sum(r["completion_rate"] for r in plan_rows) / n

    # Day-of-week breakdown
    dow_buckets: dict[str, list[float]] = {}
    for r in plan_rows:
        dow = r["day_of_week"]
        dow_buckets.setdefault(dow, []).append(r["completion_rate"])
    completion_by_dow = {
        dow: round(sum(rates) / len(rates), 2)
        for dow, rates in dow_buckets.items()
    }

    # Overcommit: planned > 6 AND completion < 60%
    overcommit_days = sum(
        1 for r in plan_rows if r["planned_count"] > 6 and r["completion_rate"] < 0.6
    )

    # Category drop-rate analysis (only categories with >= 3 data points)
    cat_totals: dict[str, int] = {}
    cat_dropped: dict[str, int] = {}
    for r in plan_rows:
        for item in r["items"]:
            cat = _categorize(item.get("description", ""))
            cat_totals[cat] = cat_totals.get(cat, 0) + 1
            if not item.get("completed"):
                cat_dropped[cat] = cat_dropped.get(cat, 0) + 1

    most_dropped = []
    for cat, total in cat_totals.items():
        if total < 3:
            continue
        drop_rate = cat_dropped.get(cat, 0) / total
        if drop_rate > 0.2:
            most_dropped.append({"category": cat, "drop_rate": round(drop_rate, 2)})
    most_dropped.sort(key=lambda x: x["drop_rate"], reverse=True)

    avg_unplanned = sum(r["unplanned_count"] for r in plan_rows) / n

    # Generate planning insight (only when >= 5 days of data)
    planning_insight: str | None = None
    if n >= 5 and completion_by_dow:
        worst_dow = min(completion_by_dow, key=completion_by_dow.get)
        best_dow = max(completion_by_dow, key=completion_by_dow.get)
        worst_rate = completion_by_dow[worst_dow]
        best_rate = completion_by_dow[best_dow]

        # Avg planned items on worst day
        worst_rows = [r for r in plan_rows if r["day_of_week"] == worst_dow]
        avg_on_worst = sum(r["planned_count"] for r in worst_rows) / len(worst_rows)

        if worst_rate < 0.6 and avg_on_worst > 4:
            suggested_lo = max(3, round(avg_on_worst * worst_rate))
            suggested_hi = suggested_lo + 1
            planning_insight = (
                f"You tend to overcommit on {worst_dow}s "
                f"(avg {avg_on_worst:.0f} items, {round(worst_rate * 100)}% completion). "
                f"Consider planning {suggested_lo}–{suggested_hi} items instead."
            )
        elif avg_completion < 0.6:
            planning_insight = (
                f"You average {round(avg_completion * 100)}% plan completion. "
                f"Your best day is {best_dow} ({round(best_rate * 100)}%). "
                f"Consider planning fewer items overall."
            )
        else:
            planning_insight = (
                f"You average {round(avg_completion * 100)}% plan completion "
                f"over the last {n} days. "
                f"Best day: {best_dow} ({round(best_rate * 100)}%)."
            )

    return {
        "days_with_plans": n,
        "avg_planned_items": round(avg_planned, 1),
        "avg_completion_rate": round(avg_completion, 2),
        "completion_by_day_of_week": completion_by_dow,
        "most_dropped_categories": most_dropped,
        "overcommit_days": overcommit_days,
        "avg_unplanned_completions": round(avg_unplanned, 1),
        "planning_insight": planning_insight,
    }
