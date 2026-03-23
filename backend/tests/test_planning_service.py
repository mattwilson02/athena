"""Tests for planning_service.py — daily briefing, plan-reality comparison, and patterns."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.planning_service import (
    compile_briefing,
    compare_plan_reality,
    analyze_plan_patterns,
    _is_habit_due_today,
    _extract_time,
)


# ── Helpers ──


class FakeGraph:
    """Minimal graph for planning service tests."""

    def __init__(self, nodes: list[dict], edges: list[tuple] | None = None):
        self._nodes = {n["id"]: n for n in nodes}
        self._edges: list[tuple[str, str, str]] = edges or []

    def get_nodes_by_type(self, node_type: str) -> list[dict]:
        return [n for n in self._nodes.values() if n.get("type") == node_type]

    def get_node(self, node_id: str) -> dict | None:
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> list[dict]:
        return list(self._nodes.values())

    def get_neighbors_with_edges(self, node_id: str) -> list[dict]:
        neighbors = []
        seen: set[str] = set()
        for source, target, edge_type in self._edges:
            if source == node_id and target not in seen:
                node = self._nodes.get(target)
                if node:
                    n = dict(node)
                    n["_edge_type"] = edge_type
                    n["_edge_direction"] = "outgoing"
                    neighbors.append(n)
                    seen.add(target)
        for source, target, edge_type in self._edges:
            if target == node_id and source not in seen:
                node = self._nodes.get(source)
                if node:
                    n = dict(node)
                    n["_edge_type"] = edge_type
                    n["_edge_direction"] = "incoming"
                    neighbors.append(n)
                    seen.add(source)
        return neighbors


def _make_node(nid: str, ntype: str, title: str, **kwargs) -> dict:
    return {"id": nid, "type": ntype, "title": title, "content": "", **kwargs}


TODAY = date(2026, 3, 23)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)


def _empty_accountability() -> dict:
    return {"streaks": [], "overdue": [], "fundamentals": []}


# ── compile_briefing — Events ──


class TestBriefingEvents:
    def test_briefing_events_for_today(self):
        event = _make_node("dentist", "event", "Dentist", date=TODAY.isoformat(), status="upcoming")
        graph = FakeGraph([event])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert len(result["events"]) == 1
        assert result["events"][0]["node_id"] == "dentist"

    def test_briefing_events_exclude_other_dates(self):
        event_tomorrow = _make_node("gym-event", "event", "Gym", date=TOMORROW.isoformat(), status="upcoming")
        graph = FakeGraph([event_tomorrow])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["events"] == []

    def test_briefing_event_time_extraction(self):
        event = _make_node("meeting", "event", "Team Meeting", date=f"{TODAY.isoformat()}T14:30", status="upcoming")
        graph = FakeGraph([event])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["events"][0]["time"] == "14:30"

    def test_briefing_event_no_time_for_date_only(self):
        event = _make_node("party", "event", "Party", date=TODAY.isoformat(), status="upcoming")
        graph = FakeGraph([event])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["events"][0]["time"] is None

    def test_briefing_events_sorted_by_time(self):
        e1 = _make_node("e1", "event", "Late", date=f"{TODAY.isoformat()}T18:00", status="upcoming")
        e2 = _make_node("e2", "event", "Early", date=f"{TODAY.isoformat()}T09:00", status="upcoming")
        e3 = _make_node("e3", "event", "No Time", date=TODAY.isoformat(), status="upcoming")
        graph = FakeGraph([e1, e2, e3])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        ids = [e["node_id"] for e in result["events"]]
        assert ids.index("e2") < ids.index("e1")   # timed events first
        assert ids.index("e3") == len(ids) - 1      # untimed event last


# ── compile_briefing — Due Tasks ──


class TestBriefingDueTasks:
    def test_briefing_due_tasks_today(self):
        task = _make_node("pr-review", "task", "Review PR", status="todo", priority="high", due=TODAY.isoformat())
        graph = FakeGraph([task])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert len(result["due_tasks"]) == 1
        assert result["due_tasks"][0]["node_id"] == "pr-review"
        assert result["due_tasks"][0]["days_overdue"] == 0

    def test_briefing_due_tasks_excludes_done(self):
        task = _make_node("done-task", "task", "Done Task", status="done", priority="high", due=TODAY.isoformat())
        graph = FakeGraph([task])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["due_tasks"] == []

    def test_briefing_due_tasks_excludes_cancelled(self):
        task = _make_node("cancelled-task", "task", "Cancelled", status="cancelled", priority="medium", due=TODAY.isoformat())
        graph = FakeGraph([task])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["due_tasks"] == []

    def test_briefing_overdue_tasks_included(self):
        task = _make_node("overdue-task", "task", "Overdue", status="todo", priority="medium",
                          due=YESTERDAY.isoformat())
        graph = FakeGraph([task])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert len(result["due_tasks"]) == 1
        assert result["due_tasks"][0]["days_overdue"] == 1

    def test_briefing_tasks_sorted_by_priority(self):
        t_low = _make_node("t-low", "task", "Low", status="todo", priority="low", due=TODAY.isoformat())
        t_med = _make_node("t-med", "task", "Med", status="todo", priority="medium", due=TODAY.isoformat())
        t_high = _make_node("t-high", "task", "High", status="todo", priority="high", due=TODAY.isoformat())
        graph = FakeGraph([t_low, t_med, t_high])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        ids = [t["node_id"] for t in result["due_tasks"]]
        assert ids.index("t-high") < ids.index("t-med") < ids.index("t-low")

    def test_briefing_task_future_due_excluded(self):
        task = _make_node("future-task", "task", "Future", status="todo", priority="high", due=TOMORROW.isoformat())
        graph = FakeGraph([task])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["due_tasks"] == []

    def test_briefing_task_no_due_excluded(self):
        task = _make_node("no-due", "task", "No Due", status="todo", priority="high")
        graph = FakeGraph([task])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["due_tasks"] == []


# ── compile_briefing — Habit Targets ──


class TestBriefingHabitTargets:
    def _streak(self, habit_id, frequency, days_since_last):
        return {
            "habit_id": habit_id,
            "habit_title": habit_id.replace("-", " ").title(),
            "frequency": frequency,
            "status": "active",
            "current_streak": 5,
            "last_completed": (TODAY - timedelta(days=days_since_last)).isoformat() if days_since_last is not None else None,
            "days_since_last": days_since_last,
            "streak_status": "on_track",
        }

    def test_briefing_habit_targets_daily(self):
        streaks = [self._streak("meditation", "daily", 0)]
        result = compile_briefing(FakeGraph([]), {"streaks": streaks, "overdue": [], "fundamentals": []}, TODAY)
        assert len(result["habit_targets"]) == 1
        assert result["habit_targets"][0]["habit_id"] == "meditation"
        assert result["habit_targets"][0]["due_today"] is True

    def test_briefing_habit_targets_weekly_due(self):
        # Last completed 6 days ago → weekly habit is due (>= 5 days)
        streaks = [self._streak("strength-training", "weekly", 6)]
        result = compile_briefing(FakeGraph([]), {"streaks": streaks, "overdue": [], "fundamentals": []}, TODAY)
        assert len(result["habit_targets"]) == 1

    def test_briefing_habit_targets_weekly_not_due(self):
        # Last completed yesterday → weekly habit is NOT due (1 < 5)
        streaks = [self._streak("strength-training", "weekly", 1)]
        result = compile_briefing(FakeGraph([]), {"streaks": streaks, "overdue": [], "fundamentals": []}, TODAY)
        assert result["habit_targets"] == []

    def test_briefing_habit_targets_3x_week_due(self):
        # 3x/week, last completed 3 days ago → due (>= 2)
        streaks = [self._streak("gym", "3x/week", 3)]
        result = compile_briefing(FakeGraph([]), {"streaks": streaks, "overdue": [], "fundamentals": []}, TODAY)
        assert len(result["habit_targets"]) == 1

    def test_briefing_habit_targets_3x_week_not_due(self):
        # 3x/week, last completed yesterday → not due (1 < 2)
        streaks = [self._streak("gym", "3x/week", 1)]
        result = compile_briefing(FakeGraph([]), {"streaks": streaks, "overdue": [], "fundamentals": []}, TODAY)
        assert result["habit_targets"] == []

    def test_briefing_habit_targets_no_completions_always_due(self):
        streaks = [self._streak("new-habit", "weekly", None)]
        streaks[0]["last_completed"] = None
        result = compile_briefing(FakeGraph([]), {"streaks": streaks, "overdue": [], "fundamentals": []}, TODAY)
        assert len(result["habit_targets"]) == 1

    def test_briefing_habit_targets_sorted_urgency_first(self):
        # habit-ok: weekly, 6 days since last → due (>= 5), on_track
        # habit-broken: daily, 5 days since last → due (always), broken
        s_on_track = {**self._streak("habit-ok", "weekly", 6), "streak_status": "on_track", "current_streak": 10}
        s_broken = {**self._streak("habit-broken", "daily", 5), "streak_status": "broken", "current_streak": 0}
        result = compile_briefing(FakeGraph([]), {"streaks": [s_on_track, s_broken], "overdue": [], "fundamentals": []}, TODAY)
        ids = [h["habit_id"] for h in result["habit_targets"]]
        assert ids.index("habit-broken") < ids.index("habit-ok")


# ── compile_briefing — Active Plan ──


class TestBriefingActivePlan:
    def test_briefing_active_plan_from_daily(self):
        daily = _make_node(
            "daily-mar-23", "daily", "March 23",
            date=TODAY.isoformat(),
            planned=[
                {"description": "Gym session", "linked_node": "strength-training", "completed": False},
                {"description": "Read 30 pages", "linked_node": None, "completed": True},
            ],
        )
        graph = FakeGraph([daily])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["active_plan"] is not None
        assert result["active_plan"]["daily_id"] == "daily-mar-23"
        assert len(result["active_plan"]["planned"]) == 2
        assert result["active_plan"]["completion_rate"] == 0.5

    def test_briefing_no_daily_node(self):
        graph = FakeGraph([])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["active_plan"] is None

    def test_briefing_daily_without_planned_field(self):
        daily = _make_node("daily-today", "daily", "Today", date=TODAY.isoformat())
        graph = FakeGraph([daily])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["active_plan"] is None

    def test_briefing_active_plan_completion_via_linked_node_status(self):
        habit = _make_node("strength-training", "habit", "Strength Training", status="done")
        daily = _make_node(
            "daily-today", "daily", "Today",
            date=TODAY.isoformat(),
            planned=[{"description": "Gym", "linked_node": "strength-training", "completed": False}],
        )
        graph = FakeGraph([daily, habit])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        # linked node has status done → completed
        assert result["active_plan"]["planned"][0]["completed"] is True
        assert result["active_plan"]["completion_rate"] == 1.0


# ── compile_briefing — Yesterday Review ──


class TestBriefingYesterdayReview:
    def test_briefing_yesterday_review(self):
        daily_yesterday = _make_node(
            "daily-yesterday", "daily", "Yesterday",
            date=YESTERDAY.isoformat(),
            planned=[
                {"description": "Gym", "linked_node": None, "completed": True},
                {"description": "Read", "linked_node": None, "completed": False},
                {"description": "Meditate", "linked_node": None, "completed": False},
            ],
        )
        graph = FakeGraph([daily_yesterday])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["yesterday_review"] is not None
        assert result["yesterday_review"]["planned_count"] == 3
        assert result["yesterday_review"]["completed_count"] == 1
        assert abs(result["yesterday_review"]["completion_rate"] - 1 / 3) < 0.01
        assert "Read" in result["yesterday_review"]["missed"]
        assert "Meditate" in result["yesterday_review"]["missed"]

    def test_briefing_no_yesterday_daily_node(self):
        graph = FakeGraph([])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["yesterday_review"] is None

    def test_briefing_yesterday_without_planned_field(self):
        daily_yesterday = _make_node("daily-yesterday", "daily", "Yesterday", date=YESTERDAY.isoformat())
        graph = FakeGraph([daily_yesterday])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["yesterday_review"] is None


# ── compile_briefing — Empty graph ──


class TestBriefingEmptyGraph:
    def test_briefing_empty_graph(self):
        graph = FakeGraph([])
        result = compile_briefing(graph, _empty_accountability(), TODAY)
        assert result["events"] == []
        assert result["due_tasks"] == []
        assert result["habit_targets"] == []
        assert result["active_plan"] is None
        assert result["yesterday_review"] is None
        assert result["overdue_summary"]["count"] == 0
        assert result["date"] == TODAY.isoformat()
        assert result["day_of_week"] == TODAY.strftime("%A")


# ── _process_plan_updates (via ChatService) ──


class TestProcessPlanUpdates:
    """Test ChatService._process_plan_updates() directly."""

    def _service(self, nodes=None):
        """Build a minimal ChatService with a fake graph."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from services.chat_service import ChatService
        from unittest.mock import MagicMock

        graph = FakeGraph(nodes or [])
        service = ChatService.__new__(ChatService)
        service.graph = graph
        return service

    def test_process_plan_valid(self):
        svc = self._service()
        updates = [{
            "action": "create",
            "type": "daily",
            "node_id": "daily-today",
            "title": "Today",
            "frontmatter": {
                "date": TODAY.isoformat(),
                "planned": [
                    {"description": "Gym session", "linked_node": "strength-training", "completed": False},
                ],
            },
        }]
        result = svc._process_plan_updates(updates)
        planned = result[0]["frontmatter"]["planned"]
        assert len(planned) == 1
        assert planned[0]["description"] == "Gym session"
        assert planned[0]["linked_node"] == "strength-training"
        assert planned[0]["completed"] is False

    def test_process_plan_missing_description(self):
        svc = self._service()
        updates = [{
            "action": "create",
            "type": "daily",
            "node_id": "daily-today",
            "title": "Today",
            "frontmatter": {
                "date": TODAY.isoformat(),
                "planned": [
                    {"linked_node": "strength-training", "completed": False},  # no description
                    {"description": "Valid item", "completed": False},
                ],
            },
        }]
        result = svc._process_plan_updates(updates)
        planned = result[0]["frontmatter"]["planned"]
        assert len(planned) == 1
        assert planned[0]["description"] == "Valid item"

    def test_process_plan_completed_default_false(self):
        svc = self._service()
        updates = [{
            "action": "create",
            "type": "daily",
            "node_id": "daily-today",
            "title": "Today",
            "frontmatter": {
                "date": TODAY.isoformat(),
                "planned": [
                    {"description": "Read a book"},  # no completed field
                ],
            },
        }]
        result = svc._process_plan_updates(updates)
        planned = result[0]["frontmatter"]["planned"]
        assert planned[0]["completed"] is False

    def test_process_plan_deduplication(self):
        svc = self._service()
        updates = [{
            "action": "create",
            "type": "daily",
            "node_id": "daily-today",
            "title": "Today",
            "frontmatter": {
                "date": TODAY.isoformat(),
                "planned": [
                    {"description": "Gym session", "completed": False},
                    {"description": "Gym Session", "completed": False},  # duplicate (case)
                    {"description": "Read", "completed": False},
                ],
            },
        }]
        result = svc._process_plan_updates(updates)
        planned = result[0]["frontmatter"]["planned"]
        assert len(planned) == 2
        descriptions = {p["description"] for p in planned}
        assert "Read" in descriptions

    def test_process_plan_invalid_linked_node(self):
        svc = self._service()
        updates = [{
            "action": "create",
            "type": "daily",
            "node_id": "daily-today",
            "title": "Today",
            "frontmatter": {
                "date": TODAY.isoformat(),
                "planned": [
                    {"description": "Task item", "linked_node": 12345, "completed": False},  # int
                ],
            },
        }]
        result = svc._process_plan_updates(updates)
        planned = result[0]["frontmatter"]["planned"]
        assert planned[0]["linked_node"] is None

    def test_process_plan_skips_non_daily_nodes(self):
        svc = self._service()
        updates = [{
            "action": "create",
            "type": "task",  # not daily
            "node_id": "some-task",
            "title": "Task",
            "frontmatter": {
                "planned": [{"description": "Item"}],
            },
        }]
        result = svc._process_plan_updates(updates)
        # Planned list for non-daily node should be untouched
        assert result[0]["frontmatter"]["planned"] == [{"description": "Item"}]

    def test_process_plan_update_action(self):
        """update action targeting daily node should also be processed."""
        daily_node = _make_node("daily-today", "daily", "Today")
        svc = self._service(nodes=[daily_node])
        updates = [{
            "action": "update",
            "node_id": "daily-today",
            "changes": {
                "frontmatter": {
                    "planned": [
                        {"description": "Run", "completed": False},
                        {"description": "Run", "completed": False},  # dup
                    ]
                }
            },
        }]
        result = svc._process_plan_updates(updates)
        planned = result[0]["changes"]["frontmatter"]["planned"]
        assert len(planned) == 1


# ── Format spec contains DAILY PLANS ──


class TestFormatSpec:
    def test_format_spec_contains_daily_plans(self):
        from mentor_agent import _GRAPH_INSTRUCTIONS
        assert "DAILY PLANS" in _GRAPH_INSTRUCTIONS
        assert "today I'm going to" in _GRAPH_INSTRUCTIONS
        assert "linked_node" in _GRAPH_INSTRUCTIONS


# ── compare_plan_reality ──


class TestComparePlanReality:
    def _daily(self, nid, planned_items):
        return _make_node(nid, "daily", nid, date=TODAY.isoformat(), planned=planned_items)

    def test_compare_plan_all_completed(self):
        daily = self._daily("d1", [
            {"description": "Gym", "linked_node": None, "completed": True},
            {"description": "Read", "linked_node": None, "completed": True},
            {"description": "Meditate", "linked_node": None, "completed": True},
        ])
        graph = FakeGraph([daily])
        result = compare_plan_reality(graph, "d1")
        assert result is not None
        assert result["completion_rate"] == 1.0
        assert result["completed_count"] == 3

    def test_compare_plan_partial(self):
        daily = self._daily("d1", [
            {"description": "Gym", "linked_node": None, "completed": True},
            {"description": "Read", "linked_node": None, "completed": False},
            {"description": "Email", "linked_node": None, "completed": True},
            {"description": "Code", "linked_node": None, "completed": False},
        ])
        graph = FakeGraph([daily])
        result = compare_plan_reality(graph, "d1")
        assert result["completion_rate"] == 0.5
        assert result["completed_count"] == 2

    def test_compare_plan_marked_source(self):
        daily = self._daily("d1", [
            {"description": "Gym", "linked_node": None, "completed": True},
        ])
        graph = FakeGraph([daily])
        result = compare_plan_reality(graph, "d1")
        assert result["items"][0]["source"] == "plan_marked"

    def test_compare_plan_status_change_source(self):
        task = _make_node("pr-review", "task", "PR Review", status="done")
        daily = self._daily("d1", [
            {"description": "Review PR", "linked_node": "pr-review", "completed": False},
        ])
        graph = FakeGraph([daily, task])
        result = compare_plan_reality(graph, "d1")
        assert result["items"][0]["completed"] is True
        assert result["items"][0]["source"] == "status_change"

    def test_compare_plan_daily_linked_source(self):
        habit = _make_node("gym-habit", "habit", "Gym", status="active")
        daily = self._daily("d1", [
            {"description": "Gym session", "linked_node": "gym-habit", "completed": False},
        ])
        # Add a Related edge from daily to habit
        graph = FakeGraph([daily, habit], edges=[("d1", "gym-habit", "relates_to")])
        result = compare_plan_reality(graph, "d1")
        assert result["items"][0]["completed"] is True
        assert result["items"][0]["source"] == "daily_linked"

    def test_compare_plan_no_linked_node(self):
        daily = self._daily("d1", [
            {"description": "Grocery shopping", "linked_node": None, "completed": False},
        ])
        graph = FakeGraph([daily])
        result = compare_plan_reality(graph, "d1")
        assert result["items"][0]["completed"] is False
        assert result["items"][0]["source"] is None

    def test_compare_plan_unplanned_completions(self):
        task_done = _make_node("call-dentist", "task", "Call Dentist",
                               status="done", updated=TODAY.isoformat())
        daily = self._daily("d1", [
            {"description": "Gym", "linked_node": None, "completed": False},
        ])
        graph = FakeGraph([daily, task_done])
        result = compare_plan_reality(graph, "d1")
        assert len(result["unplanned_completions"]) == 1
        assert result["unplanned_completions"][0]["node_id"] == "call-dentist"

    def test_compare_plan_no_plan(self):
        daily = _make_node("d1", "daily", "Today", date=TODAY.isoformat())  # no planned field
        graph = FakeGraph([daily])
        result = compare_plan_reality(graph, "d1")
        assert result is None

    def test_compare_plan_nonexistent_node(self):
        graph = FakeGraph([])
        result = compare_plan_reality(graph, "nonexistent")
        assert result is None


# ── analyze_plan_patterns ──


class TestAnalyzePlanPatterns:
    def _make_daily_with_plan(self, nid, day: date, items: list[dict]) -> dict:
        return _make_node(nid, "daily", nid, date=day.isoformat(), planned=items)

    def test_patterns_avg_completion(self):
        """3 days with completion rates 0.5, 0.7, 0.9 → avg ~0.7"""
        d1 = self._make_daily_with_plan("d1", date(2026, 3, 20), [
            {"description": "A", "completed": True},
            {"description": "B", "completed": False},
        ])
        d2 = self._make_daily_with_plan("d2", date(2026, 3, 21), [
            {"description": "C", "completed": True},
            {"description": "D", "completed": True},
            {"description": "E", "completed": False},
        ])
        d3 = self._make_daily_with_plan("d3", date(2026, 3, 22), [
            {"description": "F", "completed": True},
            {"description": "G", "completed": True},
            {"description": "H", "completed": True},
            {"description": "I", "completed": False},
        ])
        graph = FakeGraph([d1, d2, d3])
        result = analyze_plan_patterns(graph, lookback_days=14)
        # d1 rate = 0.5, d2 rate = 0.67, d3 rate = 0.75
        assert result["days_with_plans"] == 3
        assert abs(result["avg_completion_rate"] - (0.5 + 2 / 3 + 0.75) / 3) < 0.05

    def test_patterns_by_day_of_week(self):
        """Monday days should show their completion rates."""
        # Find a Monday in the past 14 days relative to the test anchor
        from datetime import date as _date
        import datetime
        today_real = _date.today()
        # Build a fixed date that is a Monday
        days_back = today_real.weekday()  # 0 = Monday
        monday = today_real - timedelta(days=days_back) if days_back < 7 else today_real
        if monday > today_real:
            monday -= timedelta(days=7)

        d_mon = self._make_daily_with_plan("d-mon", monday, [
            {"description": "X", "completed": True},
            {"description": "Y", "completed": False},
        ])
        graph = FakeGraph([d_mon])
        result = analyze_plan_patterns(graph, lookback_days=14)
        assert "Monday" in result["completion_by_day_of_week"]
        assert abs(result["completion_by_day_of_week"]["Monday"] - 0.5) < 0.01

    def test_patterns_overcommit_detection(self):
        """Day with 7 planned items and 40% completion → counted as overcommit."""
        from datetime import date as _date
        day = _date.today() - timedelta(days=1)
        items = [{"description": f"Item {i}", "completed": i < 3} for i in range(7)]
        # 3/7 = 43% completion
        d1 = self._make_daily_with_plan("d1", day, items)
        graph = FakeGraph([d1])
        result = analyze_plan_patterns(graph, lookback_days=14)
        assert result["overcommit_days"] == 1

    def test_patterns_insufficient_data(self):
        """Fewer than 5 days → planning_insight is None."""
        from datetime import date as _date
        today = _date.today()
        dailies = [
            self._make_daily_with_plan(f"d{i}", today - timedelta(days=i), [
                {"description": f"Item {i}", "completed": True}
            ])
            for i in range(3)
        ]
        graph = FakeGraph(dailies)
        result = analyze_plan_patterns(graph, lookback_days=14)
        assert result["planning_insight"] is None

    def test_patterns_no_plan_data(self):
        """No dailies with plans → valid default dict returned."""
        graph = FakeGraph([])
        result = analyze_plan_patterns(graph, lookback_days=14)
        assert result["days_with_plans"] == 0
        assert result["avg_completion_rate"] == 0.0
        assert result["planning_insight"] is None
        assert result["completion_by_day_of_week"] == {}

    def test_patterns_days_with_plans_count(self):
        from datetime import date as _date
        today = _date.today()
        dailies = [
            self._make_daily_with_plan(f"d{i}", today - timedelta(days=i), [
                {"description": f"Task {i}", "completed": i % 2 == 0}
            ])
            for i in range(6)
        ]
        graph = FakeGraph(dailies)
        result = analyze_plan_patterns(graph, lookback_days=14)
        assert result["days_with_plans"] == 6

    def test_patterns_insight_generated_at_5_days(self):
        """Planning insight generated when exactly 5 days of data available."""
        from datetime import date as _date
        today = _date.today()
        dailies = [
            self._make_daily_with_plan(f"d{i}", today - timedelta(days=i), [
                {"description": f"Task {i}", "completed": True}
            ])
            for i in range(5)
        ]
        graph = FakeGraph(dailies)
        result = analyze_plan_patterns(graph, lookback_days=14)
        # With 5 days of data, insight should be generated
        assert result["planning_insight"] is not None
