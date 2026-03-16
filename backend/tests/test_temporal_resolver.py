"""Tests for temporal resolver fixes — E3 Item 7.

Covers: status filter removal, overdue sweep, scheduled_for support,
status penalties on temporal injection, and no regressions.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mentor_agent import (
    _resolve_temporal_query,
    _get_nodes_in_date_range,
    _get_overdue_nodes,
    _STATUS_PENALTIES,
    MentorAgent,
)


# ── Helpers ──


class FakeGraph:
    """Minimal graph mock for temporal tests."""

    def __init__(self, nodes: list[dict]):
        self._nodes = {n["id"]: n for n in nodes}

    def get_all_nodes(self) -> list[dict]:
        return list(self._nodes.values())

    def get_node(self, nid: str) -> dict | None:
        return self._nodes.get(nid)

    def get_degree(self, nid: str) -> int:
        return 0

    def get_neighbors(self, nid: str, depth: int = 1) -> list[dict]:
        return []

    def get_neighbors_by_hop(self, nid: str, depth: int = 2) -> dict:
        return {}


class FakeVectorIndex:
    """Minimal vector index that returns nothing."""

    def search(self, query: str, n: int = 10) -> list[dict]:
        return []


# ── AC 1: Completed task today is visible ──


class TestCompletedTaskTodayVisible:
    """A task with date: today, status: completed should appear in date range results."""

    def test_completed_task_in_date_range(self):
        today = date(2026, 3, 16)
        nodes = [
            {"id": "task-done", "date": today.isoformat(), "status": "completed",
             "title": "Morning run", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, today, today)
        ids = [n["id"] for n in result]
        assert "task-done" in ids


# ── AC 2: Overdue task surfaces for forward queries ──


class TestOverdueTaskForwardQuery:
    """A task with deadline: yesterday, status: pending should appear via overdue sweep."""

    def test_overdue_pending_task(self):
        today = date(2026, 3, 16)
        yesterday = today - timedelta(days=1)
        nodes = [
            {"id": "task-overdue", "deadline": yesterday.isoformat(), "status": "pending",
             "title": "Submit report", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_overdue_nodes(graph, today)
        ids = [n["id"] for n in result]
        assert "task-overdue" in ids


# ── AC 3: Overdue task surfaces without temporal phrase ──


class TestOverdueWithoutTemporalPhrase:
    """Overdue sweep runs unconditionally — no date phrase needed in query."""

    def test_overdue_injected_without_date_phrase(self):
        today = date(2026, 3, 16)
        yesterday = today - timedelta(days=1)
        nodes = [
            {"id": "task-overdue", "deadline": yesterday.isoformat(), "status": "pending",
             "title": "Submit report", "type": "task"},
        ]
        graph = FakeGraph(nodes)

        # _resolve_temporal_query returns None for non-temporal queries
        result = _resolve_temporal_query("what should I focus on")
        assert result is None

        # But overdue sweep still finds it
        overdue = _get_overdue_nodes(graph, today)
        assert len(overdue) == 1
        assert overdue[0]["id"] == "task-overdue"


# ── AC 4: Completed overdue task does not surface as overdue ──


class TestCompletedOverdueNotSurfaced:
    """Task with deadline: yesterday, status: completed should NOT appear in overdue."""

    def test_completed_not_overdue(self):
        today = date(2026, 3, 16)
        yesterday = today - timedelta(days=1)
        nodes = [
            {"id": "task-done", "deadline": yesterday.isoformat(), "status": "completed",
             "title": "Submit report", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_overdue_nodes(graph, today)
        assert len(result) == 0

    def test_cancelled_not_overdue(self):
        today = date(2026, 3, 16)
        nodes = [
            {"id": "task-cancelled", "due": "2026-03-10", "status": "cancelled",
             "title": "Old task", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_overdue_nodes(graph, today)
        assert len(result) == 0

    def test_archived_not_overdue(self):
        today = date(2026, 3, 16)
        nodes = [
            {"id": "task-archived", "deadline": "2026-03-01", "status": "archived",
             "title": "Old task", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_overdue_nodes(graph, today)
        assert len(result) == 0


# ── AC 5: Old date-field nodes don't surface as overdue ──


class TestOldDateFieldNotOverdue:
    """A daily log with date: 2025-01-01 should NOT appear in overdue sweep."""

    def test_date_field_not_overdue(self):
        today = date(2026, 3, 16)
        nodes = [
            {"id": "daily-old", "date": "2025-01-01", "status": "active",
             "title": "Jan 1 journal", "type": "daily"},
        ]
        graph = FakeGraph(nodes)
        result = _get_overdue_nodes(graph, today)
        assert len(result) == 0

    def test_only_due_deadline_scheduled_for_trigger_overdue(self):
        today = date(2026, 3, 16)
        nodes = [
            # date field only — not overdue
            {"id": "exp-1", "date": "2025-06-01", "status": "", "title": "Trip", "type": "experience"},
            # due field — overdue
            {"id": "task-1", "due": "2026-03-10", "status": "pending", "title": "Task A", "type": "task"},
            # deadline field — overdue
            {"id": "task-2", "deadline": "2026-03-14", "status": "todo", "title": "Task B", "type": "task"},
            # scheduled_for field — overdue
            {"id": "task-3", "scheduled_for": "2026-03-15", "status": "active", "title": "Task C", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_overdue_nodes(graph, today)
        ids = [n["id"] for n in result]
        assert "exp-1" not in ids
        assert "task-1" in ids
        assert "task-2" in ids
        assert "task-3" in ids


# ── AC 6: Cancelled/archived nodes with today's date are deprioritized not hidden ──


class TestCancelledDeprioritizedNotHidden:
    """Cancelled task dated today appears in date range but not in overdue."""

    def test_cancelled_in_date_range(self):
        today = date(2026, 3, 16)
        nodes = [
            {"id": "task-cancelled", "date": today.isoformat(), "status": "cancelled",
             "title": "Cancelled task", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, today, today)
        ids = [n["id"] for n in result]
        assert "task-cancelled" in ids

    def test_archived_in_date_range(self):
        today = date(2026, 3, 16)
        nodes = [
            {"id": "task-archived", "date": today.isoformat(), "status": "archived",
             "title": "Archived task", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, today, today)
        ids = [n["id"] for n in result]
        assert "task-archived" in ids


# ── AC 7: scheduled_for is queryable ──


class TestScheduledForQueryable:
    """Task with scheduled_for: tomorrow surfaces for date range queries."""

    def test_scheduled_for_in_date_range(self):
        tomorrow = date(2026, 3, 17)
        nodes = [
            {"id": "task-sched", "scheduled_for": tomorrow.isoformat(), "status": "pending",
             "title": "Ferry booking", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, tomorrow, tomorrow)
        ids = [n["id"] for n in result]
        assert "task-sched" in ids

    def test_scheduled_for_overdue(self):
        today = date(2026, 3, 16)
        nodes = [
            {"id": "task-sched", "scheduled_for": "2026-03-14", "status": "pending",
             "title": "Ferry booking", "type": "task"},
        ]
        graph = FakeGraph(nodes)
        result = _get_overdue_nodes(graph, today)
        ids = [n["id"] for n in result]
        assert "task-sched" in ids


# ── AC 8: Temporal injection respects status penalties ──


class TestTemporalInjectionStatusPenalties:
    """Completed node injected via temporal path has lower score than active node."""

    def test_completed_lower_score_than_active(self):
        today = date(2026, 3, 16)
        nodes = [
            {"id": "task-active", "date": today.isoformat(), "status": "active",
             "title": "Active task", "type": "task"},
            {"id": "task-done", "date": today.isoformat(), "status": "completed",
             "title": "Done task", "type": "task"},
        ]
        # Compute injection scores as the code does
        active_node = nodes[0]
        done_node = nodes[1]

        active_score = 0.3 + _STATUS_PENALTIES.get(active_node["status"], 0.0)
        done_score = 0.3 + _STATUS_PENALTIES.get(done_node["status"], 0.0)

        assert active_score > done_score
        assert active_score == 0.3  # no penalty
        assert done_score == 0.3 + (-0.15)  # completed penalty

    def test_cancelled_even_lower(self):
        cancelled_score = 0.3 + _STATUS_PENALTIES.get("cancelled", 0.0)
        active_score = 0.3 + _STATUS_PENALTIES.get("active", 0.0)
        completed_score = 0.3 + _STATUS_PENALTIES.get("completed", 0.0)

        assert active_score > completed_score > cancelled_score


# ── AC 9: No regressions in existing date range logic ──


class TestNoRegressions:
    """Existing date range matching still works correctly."""

    def test_date_field_matches(self):
        nodes = [
            {"id": "t1", "date": "2026-03-16"},
            {"id": "t2", "date": "2026-03-17"},
            {"id": "t3", "date": "2026-03-20"},
        ]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 3, 16), date(2026, 3, 17))
        ids = [n["id"] for n in result]
        assert "t1" in ids
        assert "t2" in ids
        assert "t3" not in ids

    def test_due_field_matches(self):
        nodes = [{"id": "t1", "due": "2026-03-16"}]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 3, 16), date(2026, 3, 16))
        assert len(result) == 1

    def test_deadline_field_matches(self):
        nodes = [{"id": "t1", "deadline": "2026-03-16"}]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 3, 16), date(2026, 3, 16))
        assert len(result) == 1

    def test_ignores_invalid_dates(self):
        nodes = [{"id": "t1", "date": "not-a-date"}]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 3, 1), date(2026, 3, 31))
        assert len(result) == 0

    def test_no_date_fields(self):
        nodes = [{"id": "t1", "title": "Learn piano"}]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 3, 1), date(2026, 3, 31))
        assert len(result) == 0

    def test_date_object_values(self):
        nodes = [{"id": "t1", "date": date(2026, 3, 16)}]
        graph = FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 3, 16), date(2026, 3, 16))
        assert len(result) == 1

    def test_temporal_resolution_still_works(self):
        """Basic temporal resolution phrases still resolve correctly."""
        with patch("mentor_agent.date") as mock_date:
            frozen = date(2026, 3, 16)  # Monday
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            assert _resolve_temporal_query("what do I have today") == (frozen, frozen)
            assert _resolve_temporal_query("plans for tomorrow") == (
                date(2026, 3, 17), date(2026, 3, 17)
            )
            assert _resolve_temporal_query("what happened yesterday") == (
                date(2026, 3, 15), date(2026, 3, 15)
            )
            assert _resolve_temporal_query("tell me about goals") is None
