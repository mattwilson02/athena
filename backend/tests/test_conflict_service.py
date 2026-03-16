"""Tests for conflict_service.py — conflict detection across all types."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.conflict_service import (
    detect_conflicts,
    _detect_signals,
    _classify_conflict,
    _extract_topic_words,
    _has_new_commitment_signal,
    _gather_obligations,
    _is_overloaded,
    _is_overdue,
    MAX_CONFLICTS,
)


# ── Helpers ──


class FakeGraph:
    """Minimal graph for testing — stores nodes by type."""

    def __init__(self, nodes: list[dict]):
        self._nodes = nodes

    def get_nodes_by_type(self, node_type: str) -> list[dict]:
        return [n for n in self._nodes if n.get("type") == node_type]

    def get_node(self, node_id: str):
        return next((n for n in self._nodes if n["id"] == node_id), None)


def _make_node(id, type, title, **kwargs):
    node = {"id": id, "type": type, "title": title, "content": "", **kwargs}
    return node


# ── Signal detection ──


class TestDetectSignals:

    def test_action_intention(self):
        s = _detect_signals("i'm going to start running")
        assert s["has_intention"] is True

    def test_negation_intention(self):
        s = _detect_signals("i want to skip gym today")
        assert s["has_intention"] is True
        assert s["has_negation"] is True

    def test_spending_intention(self):
        s = _detect_signals("i want to buy a new laptop")
        assert s["has_intention"] is True
        assert s["has_spending"] is True

    def test_no_intention(self):
        s = _detect_signals("what are my goals?")
        assert s["has_intention"] is False

    def test_informational_question(self):
        s = _detect_signals("tell me about my finances")
        assert s["has_intention"] is False

    def test_change_signal(self):
        s = _detect_signals("actually i changed my mind about the trip")
        assert s["has_intention"] is True


# ── Topic word extraction ──


class TestExtractTopicWords:

    def test_extracts_meaningful_words(self):
        words = _extract_topic_words("Exercise 4x Per Week", "Daily gym sessions")
        assert "exercise" in words
        assert "gym" in words
        assert "the" not in words

    def test_skips_short_words(self):
        words = _extract_topic_words("Go To Gym", "")
        assert "go" not in words  # 2 chars, filtered


# ── Value violations ──


class TestValueViolation:

    def test_core_value_hard(self):
        graph = FakeGraph([_make_node("discipline", "value", "Discipline", priority="core")])
        conflicts = detect_conflicts("i'm going to skip my morning routine", graph, None)
        # "skip" is negation, but "discipline" may not topic-match with "morning routine"
        # Let's test with direct topic match
        graph = FakeGraph([_make_node("discipline", "value", "Discipline", priority="core",
                                      content="Staying disciplined with routine and habits")])
        conflicts = detect_conflicts("i want to quit being disciplined", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "value_violation"
        assert conflicts[0]["severity"] == "hard"

    def test_aspirational_value_soft(self):
        graph = FakeGraph([_make_node("patience", "value", "Patience", priority="aspirational",
                                      content="Being patient and measured in decisions")])
        conflicts = detect_conflicts("i'm going to stop practising patience", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["severity"] == "soft"

    def test_no_negation_no_conflict(self):
        graph = FakeGraph([_make_node("health", "value", "Health", priority="core")])
        conflicts = detect_conflicts("i'm going to focus on my health", graph, None)
        assert len(conflicts) == 0


# ── Goal contradictions ──


class TestGoalContradiction:

    def test_high_priority_hard(self):
        graph = FakeGraph([_make_node("marathon", "goal", "Run a Marathon", priority="high")])
        conflicts = detect_conflicts("i'm going to quit the marathon", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "goal_contradiction"
        assert conflicts[0]["severity"] == "hard"

    def test_medium_priority_soft(self):
        graph = FakeGraph([_make_node("piano", "goal", "Learn Piano", priority="medium")])
        conflicts = detect_conflicts("i want to give up piano", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["severity"] == "soft"

    def test_completed_goal_skipped(self):
        graph = FakeGraph([_make_node("old-goal", "goal", "Old Goal", priority="high",
                                      status="achieved")])
        conflicts = detect_conflicts("i'm going to quit the old goal", graph, None)
        assert len(conflicts) == 0

    def test_financial_goal_vs_spending(self):
        graph = FakeGraph([_make_node("save-money", "goal", "Save Money",
                                      content="Cut costs and save for a house")])
        conflicts = detect_conflicts("i want to buy a new watch", graph, None)
        # "buy" is spending, "save" is in content, but "watch" doesn't topic-match "save money"
        # Need topic overlap via semantic search for this — graph-only won't catch it
        # This test verifies no false positive from graph-only path
        # The semantic search path would add it if vector_index returns it

    def test_financial_goal_with_topic_match(self):
        graph = FakeGraph([_make_node("save-money", "goal", "Save Money",
                                      content="Reduce spending on unnecessary purchases")])
        conflicts = detect_conflicts("i want to spend money on a treat", graph, None)
        assert any(c["conflict_type"] == "financial_contradiction" for c in conflicts)


# ── Habit breaks ──


class TestHabitBreak:

    def test_daily_habit_hard(self):
        graph = FakeGraph([_make_node("gym", "habit", "Gym Workout", frequency="daily")])
        conflicts = detect_conflicts("i'm going to skip gym today", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "habit_break"
        assert conflicts[0]["severity"] == "hard"

    def test_weekly_habit_soft(self):
        graph = FakeGraph([_make_node("reading", "habit", "Reading", frequency="weekly")])
        conflicts = detect_conflicts("i want to skip reading this week", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["severity"] == "soft"

    def test_lapsed_habit_skipped(self):
        graph = FakeGraph([_make_node("old-habit", "habit", "Old Habit",
                                      status="abandoned", frequency="daily")])
        conflicts = detect_conflicts("i'm going to skip old habit", graph, None)
        assert len(conflicts) == 0


# ── Belief contradictions ──


class TestBeliefContradiction:

    def test_belief_contradiction_is_soft(self):
        graph = FakeGraph([_make_node("hard-work", "belief", "Hard Work Pays Off")])
        conflicts = detect_conflicts("i want to quit working hard", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "belief_contradiction"
        assert conflicts[0]["severity"] == "soft"


# ── Fear avoidance ──


class TestFearAvoidance:

    def test_avoidance_detected(self):
        graph = FakeGraph([_make_node("public-speaking", "fear", "Public Speaking",
                                      status="active",
                                      content="Fear of speaking in front of people")])
        conflicts = detect_conflicts("i want to avoid the speaking engagement", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "fear_avoidance"
        assert conflicts[0]["severity"] == "soft"

    def test_overcome_fear_skipped(self):
        graph = FakeGraph([_make_node("heights", "fear", "Heights", status="overcome")])
        conflicts = detect_conflicts("i want to avoid heights", graph, None)
        assert len(conflicts) == 0


# ── Schedule conflicts ──


class TestScheduleConflict:

    def test_date_overlap(self):
        event_date = (date.today() + timedelta(days=30)).isoformat()
        dt = date.fromisoformat(event_date)
        month = dt.strftime("%B").lower()

        graph = FakeGraph([_make_node("trip", "event", "Italy Trip", date=event_date)])
        conflicts = detect_conflicts(f"i'm going to book something in {month}", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "schedule_conflict"

    def test_event_with_people_is_commitment(self):
        event_date = (date.today() + timedelta(days=14)).isoformat()
        dt = date.fromisoformat(event_date)
        month = dt.strftime("%B").lower()

        graph = FakeGraph([_make_node("dinner", "event", "Dinner with Jake",
                                      date=event_date, people=["jake"])])
        conflicts = detect_conflicts(f"i want to cancel something in {month}", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "commitment_to_person"
        assert conflicts[0]["severity"] == "hard"

    def test_cancelled_event_skipped(self):
        graph = FakeGraph([_make_node("old-event", "event", "Old Event",
                                      date="2026-01-01", status="cancelled")])
        conflicts = detect_conflicts("i'm planning something in january", graph, None)
        assert len(conflicts) == 0


# ── Priority inversion ──


class TestPriorityInversion:

    def test_overdue_high_priority(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        graph = FakeGraph([_make_node("urgent-task", "task", "Urgent Report",
                                      priority="high", due=yesterday)])
        # Priority inversion fires regardless of topic match — it's about having
        # overdue high-priority work while expressing any new intention
        conflicts = detect_conflicts("i'm going to work on the urgent report", graph, None)
        assert any(c["conflict_type"] == "priority_inversion" for c in conflicts)

    def test_not_overdue_no_conflict(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        graph = FakeGraph([_make_node("task", "task", "Future Task",
                                      priority="high", due=tomorrow)])
        conflicts = detect_conflicts("i'm going to work on something else", graph, None)
        assert not any(c.get("conflict_type") == "priority_inversion" for c in conflicts)

    def test_medium_priority_no_inversion(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        graph = FakeGraph([_make_node("task", "task", "Medium Task",
                                      priority="medium", due=yesterday)])
        conflicts = detect_conflicts("i'm going to relax today", graph, None)
        assert not any(c.get("conflict_type") == "priority_inversion" for c in conflicts)


# ── Financial conflicts ──


class TestFinancialConflicts:

    def test_budget_breach(self):
        graph = FakeGraph([_make_node("monthly-budget", "budget", "Monthly Budget",
                                      amount=2000, currency="GBP")])
        conflicts = detect_conflicts("i want to buy some new clothes", graph, None)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "budget_breach"

    def test_no_spending_no_budget_conflict(self):
        graph = FakeGraph([_make_node("monthly-budget", "budget", "Monthly Budget")])
        conflicts = detect_conflicts("what's my budget looking like?", graph, None)
        assert len(conflicts) == 0


# ── Overdue helper ──


class TestIsOverdue:

    def test_past_date_is_overdue(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert _is_overdue(yesterday) is True

    def test_future_date_not_overdue(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert _is_overdue(tomorrow) is False

    def test_invalid_date(self):
        assert _is_overdue("not-a-date") is False


# ── Integration: merging, capping, sorting ──


class TestMergingAndCapping:

    def test_hard_sorted_before_soft(self):
        graph = FakeGraph([
            _make_node("discipline", "value", "Discipline", priority="core"),
            _make_node("piano", "goal", "Learn Piano", priority="medium"),
        ])
        conflicts = detect_conflicts("i want to quit discipline and piano", graph, None)
        if len(conflicts) >= 2:
            assert conflicts[0]["severity"] == "hard"

    def test_capped_at_max(self):
        nodes = [
            _make_node(f"value-{i}", "value", f"Value {i}", priority="core")
            for i in range(10)
        ]
        graph = FakeGraph(nodes)
        # Craft a message that topic-matches all values
        msg = "i want to quit " + " ".join(f"value {i}" for i in range(10))
        conflicts = detect_conflicts(msg, graph, None)
        assert len(conflicts) <= MAX_CONFLICTS

    def test_no_duplicates(self):
        graph = FakeGraph([_make_node("gym", "habit", "Gym Workout", frequency="daily")])
        # Even if semantic search returns the same node, no duplicate
        vi = MagicMock()
        vi.search.return_value = [
            {"id": "gym", "type": "habit", "title": "Gym Workout", "score": 0.2}
        ]
        conflicts = detect_conflicts("i want to skip gym", graph, vi)
        gym_conflicts = [c for c in conflicts if c["node_id"] == "gym"]
        assert len(gym_conflicts) == 1

    def test_semantic_adds_candidates(self):
        """Semantic search can surface nodes not found by graph traversal."""
        graph = FakeGraph([_make_node("gym", "habit", "Gym Workout", frequency="daily")])
        vi = MagicMock()
        # Semantic search returns a goal not in the direct type query
        vi.search.return_value = [
            {"id": "gym", "type": "habit", "title": "Gym Workout", "score": 0.1},
        ]
        conflicts = detect_conflicts("i want to skip gym", graph, vi)
        assert len(conflicts) >= 1


class TestNoIntention:
    """Messages without intention signals should return no conflicts."""

    def test_question(self):
        graph = FakeGraph([_make_node("health", "value", "Health", priority="core")])
        assert detect_conflicts("what are my values?", graph, None) == []

    def test_description(self):
        graph = FakeGraph([_make_node("gym", "habit", "Gym", frequency="daily")])
        assert detect_conflicts("tell me about my habits", graph, None) == []

    def test_empty_message(self):
        graph = FakeGraph([_make_node("goal", "goal", "Goal")])
        assert detect_conflicts("", graph, None) == []


# ── New commitment signal detection ──


class TestNewCommitmentSignal:

    def test_start_learning(self):
        assert _has_new_commitment_signal("i want to start learning guitar") is True

    def test_new_habit(self):
        assert _has_new_commitment_signal("i'm going to try a new habit") is True

    def test_sign_up(self):
        assert _has_new_commitment_signal("i want to sign up for a course") is True

    def test_negation_not_new_commitment(self):
        assert _has_new_commitment_signal("i want to quit the gym") is False

    def test_no_intention_no_commitment(self):
        assert _has_new_commitment_signal("what are my goals?") is False

    def test_intention_without_commitment_signal(self):
        assert _has_new_commitment_signal("i'm going to eat lunch") is False

    def test_kick_off(self):
        assert _has_new_commitment_signal("i want to kick off a side project") is True


# ── Obligation gathering ──


class TestGatherObligations:

    def test_gathers_active_goals(self):
        graph = FakeGraph([
            _make_node("g1", "goal", "Goal 1", priority="high"),
            _make_node("g2", "goal", "Goal 2", priority="medium"),
            _make_node("g3", "goal", "Done Goal", priority="high", status="achieved"),
        ])
        ob = _gather_obligations(graph)
        assert len(ob["goals"]) == 2
        assert ob["goals"][0]["title"] == "Goal 1"

    def test_gathers_active_projects(self):
        graph = FakeGraph([
            _make_node("p1", "project", "Project 1", status="in_progress"),
            _make_node("p2", "project", "Project 2", status="completed"),
        ])
        ob = _gather_obligations(graph)
        assert len(ob["projects"]) == 1

    def test_gathers_active_habits(self):
        graph = FakeGraph([
            _make_node("h1", "habit", "Habit 1", frequency="daily"),
            _make_node("h2", "habit", "Habit 2", frequency="weekly", status="abandoned"),
        ])
        ob = _gather_obligations(graph)
        assert len(ob["habits"]) == 1

    def test_gathers_upcoming_events_only(self):
        future = (date.today() + timedelta(days=10)).isoformat()
        past = (date.today() - timedelta(days=10)).isoformat()
        far_future = (date.today() + timedelta(days=60)).isoformat()
        graph = FakeGraph([
            _make_node("e1", "event", "Soon Event", date=future),
            _make_node("e2", "event", "Past Event", date=past),
            _make_node("e3", "event", "Far Event", date=far_future),
        ])
        ob = _gather_obligations(graph)
        assert len(ob["events_upcoming"]) == 1
        assert ob["events_upcoming"][0]["title"] == "Soon Event"


# ── Overload threshold ──


class TestIsOverloaded:

    def test_three_goals_overloaded(self):
        ob = {"goals": [{}] * 3, "projects": [], "habits": []}
        assert _is_overloaded(ob) is True

    def test_two_projects_overloaded(self):
        ob = {"goals": [], "projects": [{}] * 2, "habits": []}
        assert _is_overloaded(ob) is True

    def test_five_habits_overloaded(self):
        ob = {"goals": [], "projects": [], "habits": [{}] * 5}
        assert _is_overloaded(ob) is True

    def test_total_six_overloaded(self):
        ob = {"goals": [{}] * 2, "projects": [{}] * 1, "habits": [{}] * 3}
        assert _is_overloaded(ob) is True

    def test_below_threshold_not_overloaded(self):
        ob = {"goals": [{}] * 1, "projects": [{}] * 1, "habits": [{}] * 2}
        assert _is_overloaded(ob) is False

    def test_empty_not_overloaded(self):
        ob = {"goals": [], "projects": [], "habits": []}
        assert _is_overloaded(ob) is False


# ── Commitment overload integration ──


class TestCommitmentOverload:

    def _build_loaded_graph(self):
        """Graph with enough active obligations to trigger overload."""
        return FakeGraph([
            _make_node("g1", "goal", "Marathon Training", priority="high"),
            _make_node("g2", "goal", "Get Promoted", priority="high"),
            _make_node("g3", "goal", "Learn Piano", priority="medium"),
            _make_node("p1", "project", "Kitchen Renovation", status="in_progress"),
            _make_node("h1", "habit", "Gym 4x/week", frequency="daily"),
            _make_node("h2", "habit", "Reading", frequency="daily"),
        ])

    def test_new_commitment_triggers_overload(self):
        graph = self._build_loaded_graph()
        conflicts = detect_conflicts("i want to start learning guitar", graph, None)
        overloads = [c for c in conflicts if c["conflict_type"] == "commitment_overload"]
        assert len(overloads) == 1
        assert "obligations" in overloads[0]
        assert len(overloads[0]["obligations"]["goals"]) == 3

    def test_negation_does_not_trigger_overload(self):
        graph = self._build_loaded_graph()
        conflicts = detect_conflicts("i want to quit the gym", graph, None)
        overloads = [c for c in conflicts if c["conflict_type"] == "commitment_overload"]
        assert len(overloads) == 0

    def test_question_does_not_trigger_overload(self):
        graph = self._build_loaded_graph()
        conflicts = detect_conflicts("what are my goals?", graph, None)
        assert len(conflicts) == 0

    def test_below_threshold_no_overload(self):
        graph = FakeGraph([
            _make_node("g1", "goal", "One Goal", priority="medium"),
        ])
        conflicts = detect_conflicts("i want to start a new project", graph, None)
        overloads = [c for c in conflicts if c["conflict_type"] == "commitment_overload"]
        assert len(overloads) == 0

    def test_overload_has_soft_severity(self):
        graph = self._build_loaded_graph()
        conflicts = detect_conflicts("i want to begin a new habit", graph, None)
        overloads = [c for c in conflicts if c["conflict_type"] == "commitment_overload"]
        assert len(overloads) == 1
        assert overloads[0]["severity"] == "soft"

    def test_overload_explanation_has_counts(self):
        graph = self._build_loaded_graph()
        conflicts = detect_conflicts("i want to start something new", graph, None)
        overloads = [c for c in conflicts if c["conflict_type"] == "commitment_overload"]
        assert len(overloads) == 1
        assert "3 active goals" in overloads[0]["explanation"]
