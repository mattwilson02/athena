"""Tests for accountability_service.py — streak calculation and overdue commitments."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.accountability_service import (
    calculate_streaks,
    find_overdue_commitments,
    trace_consequences,
    _get_frequency_window,
    _streak_status,
    _calculate_streak_count,
)


# ── Helpers ──


class FakeGraph:
    """Minimal graph for accountability tests — supports nodes and typed edges."""

    def __init__(self, nodes: list[dict], edges: list[tuple] | None = None):
        """
        nodes: list of node dicts (must have 'id')
        edges: list of (source_id, target_id, edge_type) tuples
        """
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


# ── Frequency window helper ──


class TestGetFrequencyWindow:
    def test_daily(self):
        assert _get_frequency_window("daily") == 1

    def test_weekly(self):
        assert _get_frequency_window("weekly") == 7

    def test_monthly(self):
        assert _get_frequency_window("monthly") == 30

    def test_nonstandard_3x_per_week(self):
        assert _get_frequency_window("3x/week") == 7

    def test_nonstandard_4x_per_week(self):
        assert _get_frequency_window("4x/week") == 7

    def test_case_insensitive(self):
        assert _get_frequency_window("Daily") == 1


# ── Streak status helper ──


class TestStreakStatus:
    def test_daily_on_track_today(self):
        assert _streak_status(0, "daily") == "on_track"

    def test_daily_at_risk_yesterday(self):
        assert _streak_status(1, "daily") == "at_risk"

    def test_daily_broken(self):
        assert _streak_status(2, "daily") == "broken"

    def test_weekly_on_track(self):
        assert _streak_status(3, "weekly") == "on_track"

    def test_weekly_at_risk(self):
        assert _streak_status(6, "weekly") == "at_risk"

    def test_weekly_broken(self):
        assert _streak_status(10, "weekly") == "broken"

    def test_monthly_on_track(self):
        assert _streak_status(20, "monthly") == "on_track"

    def test_monthly_at_risk(self):
        assert _streak_status(28, "monthly") == "at_risk"

    def test_monthly_broken(self):
        assert _streak_status(35, "monthly") == "broken"

    def test_none_is_broken(self):
        assert _streak_status(None, "daily") == "broken"

    def test_nonstandard_treated_as_weekly(self):
        assert _streak_status(3, "3x/week") == "on_track"
        assert _streak_status(10, "3x/week") == "broken"


# ── Streak count calculation ──


class TestCalculateStreakCount:
    def test_empty_dates(self):
        assert _calculate_streak_count([], 1) == 0

    def test_single_date_daily(self):
        d = [date(2026, 3, 10)]
        assert _calculate_streak_count(d, 1) == 1

    def test_consecutive_daily(self):
        # 3 consecutive days
        today = date(2026, 3, 10)
        dates = [today, today - timedelta(days=1), today - timedelta(days=2)]
        assert _calculate_streak_count(dates, 1) == 3

    def test_weekly_windows(self):
        # Day 0, day 8, day 15 → 3 consecutive 7-day windows
        today = date(2026, 3, 21)
        dates = [today, today - timedelta(days=8), today - timedelta(days=15)]
        assert _calculate_streak_count(dates, 7) == 3


# ── calculate_streaks ──


class TestCalculateStreaks:

    def test_daily_streak_consecutive(self):
        """3 consecutive daily nodes → streak 3."""
        today = date.today()
        habit = _make_node("gym", "habit", "Gym", frequency="daily", status="active")
        d1 = _make_node("d1", "daily", "Day 1", date=today.isoformat())
        d2 = _make_node("d2", "daily", "Day 2", date=(today - timedelta(days=1)).isoformat())
        d3 = _make_node("d3", "daily", "Day 3", date=(today - timedelta(days=2)).isoformat())
        edges = [("gym", "d1", "relates_to"), ("gym", "d2", "relates_to"), ("gym", "d3", "relates_to")]
        graph = FakeGraph([habit, d1, d2, d3], edges)
        results = calculate_streaks(graph)
        assert len(results) == 1
        assert results[0]["current_streak"] == 3

    def test_daily_streak_broken_by_gap(self):
        """Entries on day N, N-2, N-3 (gap at N-1) → streak 1 (only most recent)."""
        # Use a date in the past so today doesn't interfere
        recent = date.today() - timedelta(days=10)
        d1 = _make_node("d1", "daily", "Most Recent", date=recent.isoformat())
        d2 = _make_node("d2", "daily", "Two Back", date=(recent - timedelta(days=2)).isoformat())
        d3 = _make_node("d3", "daily", "Three Back", date=(recent - timedelta(days=3)).isoformat())
        habit = _make_node("gym", "habit", "Gym", frequency="daily", status="active")
        edges = [("gym", "d1", "relates_to"), ("gym", "d2", "relates_to"), ("gym", "d3", "relates_to")]
        graph = FakeGraph([habit, d1, d2, d3], edges)
        results = calculate_streaks(graph)
        assert results[0]["current_streak"] == 1

    def test_weekly_streak(self):
        """Dailies at day 0, day 8, day 15 → streak 3 (each in a 7-day window)."""
        today = date.today()
        d1 = _make_node("d1", "daily", "Day 0", date=today.isoformat())
        d2 = _make_node("d2", "daily", "Day 8", date=(today - timedelta(days=8)).isoformat())
        d3 = _make_node("d3", "daily", "Day 15", date=(today - timedelta(days=15)).isoformat())
        habit = _make_node("gym", "habit", "Gym", frequency="weekly", status="active")
        edges = [("gym", "d1", "relates_to"), ("gym", "d2", "relates_to"), ("gym", "d3", "relates_to")]
        graph = FakeGraph([habit, d1, d2, d3], edges)
        results = calculate_streaks(graph)
        assert results[0]["current_streak"] == 3

    def test_weekly_streak_broken(self):
        """Last daily 10 days ago → streak_status 'broken'."""
        today = date.today()
        d1 = _make_node("d1", "daily", "Day 10", date=(today - timedelta(days=10)).isoformat())
        habit = _make_node("gym", "habit", "Gym", frequency="weekly", status="active")
        edges = [("gym", "d1", "relates_to")]
        graph = FakeGraph([habit, d1], edges)
        results = calculate_streaks(graph)
        assert results[0]["streak_status"] == "broken"

    def test_no_linked_dailies(self):
        """Habit with zero dailies → streak 0, streak_status broken."""
        habit = _make_node("gym", "habit", "Gym", frequency="daily", status="active")
        graph = FakeGraph([habit], [])
        results = calculate_streaks(graph)
        assert len(results) == 1
        assert results[0]["current_streak"] == 0
        assert results[0]["streak_status"] == "broken"
        assert results[0]["last_completed"] is None

    def test_lapsed_habit_excluded(self):
        """Lapsed habits are not included in results."""
        habit = _make_node("gym", "habit", "Gym", frequency="daily", status="lapsed")
        graph = FakeGraph([habit], [])
        results = calculate_streaks(graph)
        assert results == []

    def test_completed_habit_excluded(self):
        """Completed habits are not included in results."""
        habit = _make_node("gym", "habit", "Gym", frequency="daily", status="completed")
        graph = FakeGraph([habit], [])
        results = calculate_streaks(graph)
        assert results == []

    def test_nonstandard_frequency_treated_as_weekly(self):
        """'3x/week' frequency uses 7-day windows, same as weekly."""
        today = date.today()
        d1 = _make_node("d1", "daily", "Day 0", date=today.isoformat())
        d2 = _make_node("d2", "daily", "Day 8", date=(today - timedelta(days=8)).isoformat())
        d3 = _make_node("d3", "daily", "Day 15", date=(today - timedelta(days=15)).isoformat())
        habit = _make_node("gym", "habit", "Gym", frequency="3x/week", status="active")
        edges = [("gym", "d1", "relates_to"), ("gym", "d2", "relates_to"), ("gym", "d3", "relates_to")]
        graph = FakeGraph([habit, d1, d2, d3], edges)
        results = calculate_streaks(graph)
        assert results[0]["current_streak"] == 3

    def test_multiple_dailies_same_date(self):
        """Two daily nodes on same date count as one occurrence → streak 1."""
        today = date.today()
        d1 = _make_node("d1", "daily", "Morning", date=today.isoformat())
        d2 = _make_node("d2", "daily", "Evening", date=today.isoformat())
        habit = _make_node("gym", "habit", "Gym", frequency="daily", status="active")
        edges = [("gym", "d1", "relates_to"), ("gym", "d2", "relates_to")]
        graph = FakeGraph([habit, d1, d2], edges)
        results = calculate_streaks(graph)
        assert results[0]["current_streak"] == 1

    def test_results_sorted_by_urgency(self):
        """Broken habits appear before on_track habits."""
        today = date.today()
        # on_track habit: linked to today's daily
        h1 = _make_node("h1", "habit", "On Track", frequency="weekly", status="active")
        d1 = _make_node("d1", "daily", "Today", date=today.isoformat())
        # broken habit: no dailies
        h2 = _make_node("h2", "habit", "Broken", frequency="weekly", status="active")
        graph = FakeGraph([h1, h2, d1], [("h1", "d1", "relates_to")])
        results = calculate_streaks(graph)
        assert len(results) == 2
        statuses = [r["streak_status"] for r in results]
        assert statuses.index("broken") < statuses.index("on_track")

    def test_daily_with_no_date_field_skipped(self):
        """Daily nodes without a 'date' field are skipped."""
        today = date.today()
        habit = _make_node("gym", "habit", "Gym", frequency="daily", status="active")
        d_no_date = _make_node("d1", "daily", "No Date")  # no date field
        d_with_date = _make_node("d2", "daily", "With Date", date=today.isoformat())
        edges = [("gym", "d1", "relates_to"), ("gym", "d2", "relates_to")]
        graph = FakeGraph([habit, d_no_date, d_with_date], edges)
        results = calculate_streaks(graph)
        assert results[0]["current_streak"] == 1  # only d2 counted

    def test_result_contains_expected_fields(self):
        """Each result dict has all expected keys."""
        habit = _make_node("gym", "habit", "Gym", frequency="daily", status="active")
        graph = FakeGraph([habit], [])
        results = calculate_streaks(graph)
        r = results[0]
        assert "habit_id" in r
        assert "habit_title" in r
        assert "frequency" in r
        assert "status" in r
        assert "current_streak" in r
        assert "last_completed" in r
        assert "days_since_last" in r
        assert "streak_status" in r


# ── find_overdue_commitments ──


class TestFindOverdueCommitments:

    def test_overdue_task_found(self):
        """Task with due yesterday and status todo → returned."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="todo")
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert len(result) == 1
        assert result[0]["node_id"] == "t1"

    def test_completed_task_not_overdue(self):
        """Task with due yesterday and status done → not returned."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="done")
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert len(result) == 0

    def test_cancelled_task_not_overdue(self):
        """Cancelled task with past due date → not returned."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="cancelled")
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert len(result) == 0

    def test_days_overdue_calculated(self):
        """Due 3 days ago → days_overdue = 3."""
        today = date.today()
        three_ago = (today - timedelta(days=3)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=three_ago, status="todo")
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert result[0]["days_overdue"] == 3

    def test_consequence_chain_1_hop(self):
        """Overdue task part_of project → project in consequences."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="todo")
        project = _make_node("p1", "project", "Project 1", status="active")
        edges = [("t1", "p1", "part_of")]
        graph = FakeGraph([task, project], edges)
        result = find_overdue_commitments(graph, today)
        assert len(result) == 1
        consequence_ids = [c["node_id"] for c in result[0]["consequences"]]
        assert "p1" in consequence_ids

    def test_consequence_chain_2_hop(self):
        """Overdue task → project → goal → goal appears in consequences (2 hops)."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="todo")
        project = _make_node("p1", "project", "Project 1", status="active")
        goal = _make_node("g1", "goal", "Goal 1", status="active")
        edges = [("t1", "p1", "part_of"), ("p1", "g1", "supported_by")]
        graph = FakeGraph([task, project, goal], edges)
        result = find_overdue_commitments(graph, today)
        consequence_ids = [c["node_id"] for c in result[0]["consequences"]]
        assert "p1" in consequence_ids
        assert "g1" in consequence_ids

    def test_consequence_chain_skips_completed(self):
        """Completed goal is not included in consequences."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="todo")
        goal = _make_node("g1", "goal", "Done Goal", status="completed")
        edges = [("t1", "g1", "supported_by")]
        graph = FakeGraph([task, goal], edges)
        result = find_overdue_commitments(graph, today)
        consequence_ids = [c["node_id"] for c in result[0]["consequences"]]
        assert "g1" not in consequence_ids

    def test_consequence_chain_max_hops(self):
        """3-hop chain with max_hops=2 → only up to 2 hops returned."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="todo")
        p1 = _make_node("p1", "project", "Project 1", status="active")
        g1 = _make_node("g1", "goal", "Goal 1", status="active")
        v1 = _make_node("v1", "value", "Value 1", status="active")  # 3rd hop
        edges = [
            ("t1", "p1", "part_of"),
            ("p1", "g1", "supported_by"),
            ("g1", "v1", "supported_by"),
        ]
        graph = FakeGraph([task, p1, g1, v1], edges)
        result = find_overdue_commitments(graph, today)
        consequence_ids = [c["node_id"] for c in result[0]["consequences"]]
        assert "p1" in consequence_ids
        assert "g1" in consequence_ids
        assert "v1" not in consequence_ids

    def test_no_due_date_skipped(self):
        """Node without due/deadline field → not returned."""
        today = date.today()
        task = _make_node("t1", "task", "Task 1", status="todo")
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert len(result) == 0

    def test_commitment_metadata_included(self):
        """Node with committed_on and commitment_context → both included in result."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node(
            "t1", "task", "Task 1",
            due=yesterday, status="todo",
            committed_on="2026-03-15",
            commitment_context="Promised Sarah by Tuesday",
        )
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert result[0]["committed_on"] == "2026-03-15"
        assert result[0]["commitment_context"] == "Promised Sarah by Tuesday"

    def test_consequences_sorted_by_permanence(self):
        """Goal (strategic) appears before task (tactical) in consequences."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="todo")
        t2 = _make_node("t2", "task", "Task 2", status="active")
        g1 = _make_node("g1", "goal", "Goal 1", status="active")
        edges = [("t1", "t2", "relates_to"), ("t1", "g1", "supported_by")]
        graph = FakeGraph([task, t2, g1], edges)
        result = find_overdue_commitments(graph, today)
        ids = [c["node_id"] for c in result[0]["consequences"]]
        assert "g1" in ids and "t2" in ids
        assert ids.index("g1") < ids.index("t2")

    def test_overdue_sorted_most_overdue_first(self):
        """Results sorted by days_overdue descending."""
        today = date.today()
        t1 = _make_node("t1", "task", "Task 1", due=(today - timedelta(days=1)).isoformat(), status="todo")
        t2 = _make_node("t2", "task", "Task 2", due=(today - timedelta(days=5)).isoformat(), status="todo")
        graph = FakeGraph([t1, t2])
        result = find_overdue_commitments(graph, today)
        assert result[0]["node_id"] == "t2"  # 5 days overdue first
        assert result[1]["node_id"] == "t1"

    def test_deadline_field_detected(self):
        """Node with 'deadline' field (not 'due') is detected as overdue."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", deadline=yesterday, status="active")
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert len(result) == 1

    def test_invalid_committed_on_not_included(self):
        """committed_on with unparseable date → committed_on is None in result."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Task 1", due=yesterday, status="todo", committed_on="not-a-date")
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert result[0]["committed_on"] is None

    def test_no_overdue_nodes(self):
        """Future due date → empty result."""
        today = date.today()
        tomorrow = (today + timedelta(days=1)).isoformat()
        task = _make_node("t1", "task", "Future Task", due=tomorrow, status="todo")
        graph = FakeGraph([task])
        result = find_overdue_commitments(graph, today)
        assert len(result) == 0


# ── trace_consequences ──


class TestTraceConsequences:

    def test_no_edges_empty_consequences(self):
        """Node with no edges → empty consequences."""
        task = _make_node("t1", "task", "Task 1", status="todo")
        graph = FakeGraph([task], [])
        result = trace_consequences(graph, "t1")
        assert result == []

    def test_non_consequence_edge_skipped(self):
        """Edges not in consequence edge types (e.g. 'met_at') are skipped."""
        task = _make_node("t1", "task", "Task 1", status="todo")
        p1 = _make_node("p1", "person", "Alice", status="active")
        edges = [("t1", "p1", "met_at")]
        graph = FakeGraph([task, p1], edges)
        result = trace_consequences(graph, "t1")
        assert len(result) == 0

    def test_cycle_protection(self):
        """Circular edges don't cause infinite loops."""
        t1 = _make_node("t1", "task", "Task 1", status="todo")
        t2 = _make_node("t2", "task", "Task 2", status="active")
        edges = [("t1", "t2", "relates_to"), ("t2", "t1", "relates_to")]
        graph = FakeGraph([t1, t2], edges)
        result = trace_consequences(graph, "t1")
        # Should not loop and should not include t1 (starting node)
        ids = [c["node_id"] for c in result]
        assert "t1" not in ids

# ── check_fundamentals ──


from services.accountability_service import check_fundamentals


def _make_habit(nid: str, title: str, tags: list | None = None, status: str = "active") -> dict:
    return {"id": nid, "type": "habit", "title": title, "tags": tags or [], "status": status, "content": ""}


def _make_daily(nid: str, date_str: str) -> dict:
    return {"id": nid, "type": "daily", "title": nid, "date": date_str, "content": ""}


class TestCheckFundamentals:

    def _graph_with_habit_and_dailies(
        self, habit: dict, daily_dates: list[str]
    ) -> "FakeGraph":
        """Build a FakeGraph with one habit linked to daily nodes."""
        nodes = [habit]
        edges: list[tuple] = []
        for i, d in enumerate(daily_dates):
            daily_id = f"daily-{i}"
            nodes.append(_make_daily(daily_id, d))
            edges.append((habit["id"], daily_id, "relates_to"))
        return FakeGraph(nodes, edges)

    def test_fundamentals_movement_neglected(self):
        """Gym habit with last daily 16 days ago → movement neglected."""
        today = date.today()
        last = (today - timedelta(days=16)).isoformat()
        habit = _make_habit("gym", "Strength Training")
        graph = self._graph_with_habit_and_dailies(habit, [last])
        result = check_fundamentals(graph, today)
        movement_entries = [f for f in result if f["fundamental"] == "movement"]
        assert len(movement_entries) == 1
        assert movement_entries[0]["status"] == "neglected"
        assert movement_entries[0]["days_since_activity"] == 16

    def test_fundamentals_movement_active(self):
        """Gym habit with daily yesterday → NOT in results (active)."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        habit = _make_habit("gym", "Gym Workout")
        graph = self._graph_with_habit_and_dailies(habit, [yesterday])
        result = check_fundamentals(graph, today)
        movement_entries = [f for f in result if f["fundamental"] == "movement"]
        assert len(movement_entries) == 0

    def test_fundamentals_movement_active_include_active(self):
        """With include_active=True, active fundamentals are returned."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        habit = _make_habit("gym", "Gym Workout")
        graph = self._graph_with_habit_and_dailies(habit, [yesterday])
        result = check_fundamentals(graph, today, include_active=True)
        movement_entries = [f for f in result if f["fundamental"] == "movement"]
        assert len(movement_entries) == 1
        assert movement_entries[0]["status"] == "active"

    def test_fundamentals_no_data(self):
        """No habits match 'nutrition' keywords → nutrition appears as no_data."""
        habit = _make_habit("gym", "Strength Training")
        graph = FakeGraph([habit], [])
        result = check_fundamentals(graph, date.today())
        nutrition_entries = [f for f in result if f["fundamental"] == "nutrition"]
        assert len(nutrition_entries) == 1
        assert nutrition_entries[0]["status"] == "no_data"

    def test_fundamentals_multiple_habits_per_category(self):
        """Two movement habits — most recent daily across both is used."""
        today = date.today()
        old_date = (today - timedelta(days=20)).isoformat()
        recent_date = (today - timedelta(days=3)).isoformat()
        habit1 = _make_habit("gym", "Gym Training")
        habit2 = _make_habit("running", "Morning Run")
        daily1 = _make_daily("d1", old_date)
        daily2 = _make_daily("d2", recent_date)
        graph = FakeGraph(
            [habit1, habit2, daily1, daily2],
            [("gym", "d1", "relates_to"), ("running", "d2", "relates_to")],
        )
        result = check_fundamentals(graph, today)
        movement_entries = [f for f in result if f["fundamental"] == "movement"]
        # Most recent daily is 3 days ago — should be active (not neglected)
        assert len(movement_entries) == 0  # active, so filtered out

    def test_fundamentals_lapsed_habit_still_counted(self):
        """Lapsed habit's dailies still count for recency."""
        today = date.today()
        recent = (today - timedelta(days=5)).isoformat()
        habit = _make_habit("gym", "Gym Training", status="lapsed")
        graph = self._graph_with_habit_and_dailies(habit, [recent])
        result = check_fundamentals(graph, today)
        # 5 days ago = active (≤14), so not in results
        movement_entries = [f for f in result if f["fundamental"] == "movement"]
        assert len(movement_entries) == 0

    def test_fundamentals_lapsed_habit_neglected(self):
        """Lapsed habit with old dailies → still shows as neglected."""
        today = date.today()
        old = (today - timedelta(days=30)).isoformat()
        habit = _make_habit("gym", "Gym Training", status="lapsed")
        graph = self._graph_with_habit_and_dailies(habit, [old])
        result = check_fundamentals(graph, today)
        movement_entries = [f for f in result if f["fundamental"] == "movement"]
        assert len(movement_entries) == 1
        assert movement_entries[0]["status"] == "neglected"

    def test_fundamentals_empty_when_all_active(self):
        """All fundamentals with recent activity → empty result."""
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        # One habit for each fundamental that has keywords
        habits_and_dailies = [
            (_make_habit("gym", "Gym Training"), yesterday),
            (_make_habit("sleep-routine", "Sleep by 11pm"), yesterday),
            (_make_habit("meal-prep", "Weekly Meal Prep"), yesterday),
            (_make_habit("friends", "Weekly dinner with friends"), yesterday),
            (_make_habit("reading", "Daily Reading"), yesterday),
            (_make_habit("budget-review", "Monthly Budget Review"), yesterday),
        ]
        nodes = []
        edges: list[tuple] = []
        for i, (habit, date_str) in enumerate(habits_and_dailies):
            nodes.append(habit)
            daily = _make_daily(f"d-{i}", date_str)
            nodes.append(daily)
            edges.append((habit["id"], daily["id"], "relates_to"))
        graph = FakeGraph(nodes, edges)
        result = check_fundamentals(graph, today)
        # All are active → only no_data entries remain (for any unmatched categories)
        neglected = [f for f in result if f["status"] == "neglected"]
        assert len(neglected) == 0

    def test_fundamentals_keyword_matching_case_insensitive(self):
        """'Strength Training' matches 'strength' keyword."""
        today = date.today()
        old = (today - timedelta(days=20)).isoformat()
        habit = _make_habit("h1", "Strength Training")
        graph = self._graph_with_habit_and_dailies(habit, [old])
        result = check_fundamentals(graph, today)
        movement_entries = [f for f in result if f["fundamental"] == "movement"]
        assert len(movement_entries) == 1
        assert movement_entries[0]["status"] == "neglected"

    def test_fundamentals_returns_related_habits(self):
        """Neglected entry includes related_habits list."""
        today = date.today()
        old = (today - timedelta(days=20)).isoformat()
        habit = _make_habit("gym-h", "Gym Session")
        graph = self._graph_with_habit_and_dailies(habit, [old])
        result = check_fundamentals(graph, today)
        movement_entries = [f for f in result if f["fundamental"] == "movement"]
        assert "gym-h" in movement_entries[0]["related_habits"]


class TestFundamentalsContentInference:
    """check_fundamentals with content inference via vault_root."""

    def test_fundamentals_content_inference(self):
        """Habit with no edges but daily content mentions activity → not neglected."""
        today = date.today()
        recent = (today - timedelta(days=2)).isoformat()
        habit = {"id": "gym", "type": "habit", "title": "Strength Training",
                 "tags": ["training", "fitness"], "status": "active", "content": ""}
        # Daily has content referencing training but no edge to habit
        daily = {
            "id": "d1", "type": "daily", "date": recent,
            "content": "training session completed",
        }
        graph = FakeGraph([habit, daily], [])  # No edges
        # vault_root="." enables content inference; content is in node dict so no file reading
        result = check_fundamentals(graph, today, include_active=True, vault_root=".")
        movement = [f for f in result if f["fundamental"] == "movement"]
        # Should be active — content inference found the recent training mention
        assert len(movement) == 1
        assert movement[0]["status"] == "active"

    def test_fundamentals_without_vault_root_edge_only(self):
        """Without vault_root, only edge-based dates count (backward compat)."""
        today = date.today()
        old = (today - timedelta(days=20)).isoformat()
        habit = {"id": "gym", "type": "habit", "title": "Strength Training",
                 "tags": ["training"], "status": "active", "content": ""}
        # Daily with content but no edge — would be found by inference but vault_root=None
        daily_content_only = {
            "id": "d1", "type": "daily", "date": old,
            "content": "training completed",
        }
        graph = FakeGraph([habit, daily_content_only], [])
        # vault_root=None → content inference disabled → no dates found → neglected
        result = check_fundamentals(graph, today, vault_root=None)
        movement = [f for f in result if f["fundamental"] == "movement"]
        assert len(movement) == 1
        assert movement[0]["status"] == "neglected"
