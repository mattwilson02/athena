"""Tests for content-based habit inference and kind-aware streak calculation."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.accountability_service import (
    _extract_habit_keywords,
    infer_habit_completions,
    calculate_streaks,
    _get_frequency_window,
)


# ── Helpers ──


class FakeGraph:
    """Minimal graph for habit inference tests."""

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
                    neighbors.append(n)
                    seen.add(target)

        for source, target, edge_type in self._edges:
            if target == node_id and source not in seen:
                node = self._nodes.get(source)
                if node:
                    n = dict(node)
                    n["_edge_type"] = edge_type
                    neighbors.append(n)
                    seen.add(source)

        return neighbors


def _make_habit(hid: str, title: str, kind: str = "build", frequency: str = "weekly",
                status: str = "active", tags: list | None = None, **kwargs) -> dict:
    return {
        "id": hid, "type": "habit", "title": title,
        "kind": kind, "frequency": frequency, "status": status,
        "tags": tags or [],
        **kwargs,
    }


def _make_daily(did: str, date_str: str, content: str = "") -> dict:
    return {"id": did, "type": "daily", "date": date_str, "content": content}


TODAY = date(2026, 3, 24)


# ── _extract_habit_keywords ──


class TestExtractKeywords:
    def test_extract_keywords_from_title_and_tags(self):
        habit = _make_habit(
            "strength-training", "Strength Training 3x/week",
            tags=["fitness", "training", "strength"],
        )
        kws = _extract_habit_keywords(habit)
        assert "strength" in kws
        assert "training" in kws
        assert "fitness" in kws

    def test_extract_keywords_removes_stopwords(self):
        habit = _make_habit("morning-routine", "My Morning Routine")
        kws = _extract_habit_keywords(habit)
        assert "my" not in kws
        assert "morning" in kws
        assert "routine" in kws

    def test_extract_keywords_removes_short(self):
        habit = _make_habit("steps-habit", "12k Steps Daily")
        kws = _extract_habit_keywords(habit)
        # "12k" is 3 chars — kept; short 2-char words like "at" would be removed
        for kw in kws:
            assert len(kw) >= 3

    def test_extract_keywords_includes_custom(self):
        habit = _make_habit(
            "strength-training", "Strength Training",
            tags=["fitness"],
            keywords=["shoulders", "arms"],
        )
        kws = _extract_habit_keywords(habit)
        assert "shoulders" in kws
        assert "arms" in kws

    def test_extract_keywords_deduplicates(self):
        habit = _make_habit(
            "training-habit", "Training Habit",
            tags=["training"],  # 'training' already in title
        )
        kws = _extract_habit_keywords(habit)
        assert kws.count("training") == 1

    def test_extract_keywords_empty_title_and_tags(self):
        habit = _make_habit("bare-habit", "")
        kws = _extract_habit_keywords(habit)
        assert isinstance(kws, list)

    def test_extract_keywords_no_tags_field(self):
        habit = {"id": "h1", "type": "habit", "title": "Morning Walk", "kind": "build"}
        kws = _extract_habit_keywords(habit)
        assert "morning" in kws
        assert "walk" in kws


# ── infer_habit_completions ──


class TestInferHabitCompletions:
    def test_infer_matches_daily_content(self):
        habit = _make_habit("strength-training", "Strength Training", tags=["training"])
        daily = _make_daily("d1", "2026-03-20", "gym session and training completed today")
        graph = FakeGraph([habit, daily])
        result = infer_habit_completions(graph, habit)
        assert date(2026, 3, 20) in result

    def test_infer_no_match_unrelated_content(self):
        habit = _make_habit("strength-training", "Strength Training", tags=["training"])
        daily = _make_daily("d1", "2026-03-20", "portfolio work and admin tasks")
        graph = FakeGraph([habit, daily])
        result = infer_habit_completions(graph, habit)
        assert date(2026, 3, 20) not in result

    def test_infer_case_insensitive(self):
        habit = _make_habit("strength-training", "Strength Training", tags=["training"])
        daily = _make_daily("d1", "2026-03-20", "TRAINING session completed")
        graph = FakeGraph([habit, daily])
        result = infer_habit_completions(graph, habit)
        assert date(2026, 3, 20) in result

    def test_infer_skips_dateless_daily(self):
        habit = _make_habit("strength-training", "Strength Training", tags=["training"])
        daily = {"id": "d1", "type": "daily", "content": "training session today"}
        graph = FakeGraph([habit, daily])
        result = infer_habit_completions(graph, habit)
        assert len(result) == 0

    def test_infer_returns_dates(self):
        habit = _make_habit("fasting", "Periodic Fasting", tags=["fasting"])
        d1 = _make_daily("d1", "2026-03-17", "started 48-hour fasting protocol tonight")
        d2 = _make_daily("d2", "2026-03-10", "ended my fasting period at 8pm")
        graph = FakeGraph([habit, d1, d2])
        result = infer_habit_completions(graph, habit)
        assert date(2026, 3, 17) in result
        assert date(2026, 3, 10) in result

    def test_infer_empty_keywords_empty_result(self):
        # Habit with only stopword title and no tags → no usable keywords
        habit = {"id": "h1", "type": "habit", "title": "My To", "kind": "build", "tags": []}
        daily = _make_daily("d1", "2026-03-20", "My To stuff today")
        graph = FakeGraph([habit, daily])
        result = infer_habit_completions(graph, habit)
        # Either empty (all keywords filtered) or matched — depends on filtering
        # "my" and "to" are both stopwords / <3 chars, so keywords should be empty
        assert result == []

    def test_infer_multiple_dailies(self):
        habit = _make_habit("nicotine", "Nicotine Pouches", kind="break",
                            tags=["nicotine", "dependency"])
        d1 = _make_daily("d1", "2026-03-12", "had a nicotine pouch at lunch")
        d2 = _make_daily("d2", "2026-03-15", "used a nicotine pouch again")
        d3 = _make_daily("d3", "2026-03-20", "clean day, no pouches")
        graph = FakeGraph([habit, d1, d2, d3])
        result = infer_habit_completions(graph, habit)
        assert date(2026, 3, 12) in result
        assert date(2026, 3, 15) in result


# ── Kind-aware streak calculation ──


_DUMMY_VAULT = "."  # vault_root must be non-None to enable inference; content in node dict


class TestBuildStreakWithContentInference:
    def test_build_streak_with_content_inference(self):
        """Habit with no edges but daily content mentions → positive streak count."""
        habit = _make_habit("strength-training", "Strength Training",
                            frequency="3x/week", tags=["training"])
        # Dailies with content, no edges to habit
        today = date.today()
        d1 = _make_daily("d1", (today - timedelta(days=1)).isoformat(), "training session done")
        d2 = _make_daily("d2", (today - timedelta(days=4)).isoformat(), "great training today")
        d3 = _make_daily("d3", (today - timedelta(days=7)).isoformat(), "training completed")
        graph = FakeGraph([habit, d1, d2, d3])
        results = calculate_streaks(graph, vault_root=_DUMMY_VAULT)
        st = next(r for r in results if r["habit_id"] == "strength-training")
        assert st["current_streak"] is not None
        assert st["current_streak"] > 0

    def test_build_streak_merges_edges_and_content(self):
        """Edge-linked date + content-inferred date → both counted, no duplicates."""
        habit = _make_habit("strength-training", "Strength Training",
                            frequency="3x/week", tags=["training"])
        today = date.today()
        # Edge-linked daily (no content)
        d_edge = _make_daily("d-edge", (today - timedelta(days=2)).isoformat(), "")
        # Content-only daily (no edge)
        d_content = _make_daily("d-content", (today - timedelta(days=5)).isoformat(),
                                "training session")
        graph = FakeGraph(
            [habit, d_edge, d_content],
            edges=[("strength-training", "d-edge", "relates_to")],
        )
        results = calculate_streaks(graph, vault_root=_DUMMY_VAULT)
        st = next(r for r in results if r["habit_id"] == "strength-training")
        assert st["current_streak"] is not None
        assert st["current_streak"] >= 1

    def test_default_kind_is_build(self):
        """Habit without kind field → treated as build."""
        habit = {"id": "no-kind", "type": "habit", "title": "No Kind Habit",
                 "frequency": "weekly", "status": "active"}
        graph = FakeGraph([habit])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "no-kind")
        assert st["kind"] == "build"
        assert "current_streak" in st

    def test_quarterly_frequency_window(self):
        assert _get_frequency_window("quarterly") == 90

    def test_result_has_all_fields(self):
        """Every result dict has all expected keys regardless of kind."""
        habit = _make_habit("h1", "Some Habit", kind="build")
        graph = FakeGraph([habit])
        results = calculate_streaks(graph)
        st = results[0]
        for field in ("habit_id", "habit_title", "frequency", "status", "kind",
                      "current_streak", "last_completed", "days_since_last",
                      "days_clean", "last_occurrence", "next_due", "days_until_due",
                      "streak_status"):
            assert field in st, f"Missing field: {field}"


class TestBreakHabitStreaks:
    """Use edge-linked dailies (no vault_root needed)."""

    def test_break_habit_days_clean(self):
        """Break habit linked daily 12 days ago → days_clean: 12."""
        habit = _make_habit("nicotine", "Nicotine Pouches", kind="break")
        last_date = date.today() - timedelta(days=12)
        daily = _make_daily("d1", last_date.isoformat(), "")
        graph = FakeGraph([habit, daily], edges=[("nicotine", "d1", "relates_to")])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "nicotine")
        assert st["kind"] == "break"
        assert st["days_clean"] == 12
        assert st["streak_status"] == "on_track"
        assert st["current_streak"] is None

    def test_break_habit_no_mentions_unknown(self):
        """Break habit with no linked dailies → days_clean: None, status: unknown."""
        habit = _make_habit("nicotine", "Nicotine Pouches", kind="break")
        graph = FakeGraph([habit])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "nicotine")
        assert st["days_clean"] is None
        assert st["streak_status"] == "unknown"

    def test_break_habit_mentioned_today_relapsed(self):
        """Break habit linked to today's daily → days_clean: 0, status: relapsed."""
        habit = _make_habit("nicotine", "Nicotine Pouches", kind="break")
        daily = _make_daily("d1", date.today().isoformat(), "")
        graph = FakeGraph([habit, daily], edges=[("nicotine", "d1", "relates_to")])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "nicotine")
        assert st["days_clean"] == 0
        assert st["streak_status"] == "relapsed"

    def test_break_habit_early_status(self):
        """Break habit linked daily 5 days ago → status: early."""
        habit = _make_habit("nicotine", "Nicotine Pouches", kind="break")
        daily = _make_daily("d1", (date.today() - timedelta(days=5)).isoformat(), "")
        graph = FakeGraph([habit, daily], edges=[("nicotine", "d1", "relates_to")])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "nicotine")
        assert st["days_clean"] == 5
        assert st["streak_status"] == "early"

    def test_break_habit_strong_status(self):
        """Break habit linked daily 35 days ago → status: strong."""
        habit = _make_habit("nicotine", "Nicotine Pouches", kind="break")
        daily = _make_daily("d1", (date.today() - timedelta(days=35)).isoformat(), "")
        graph = FakeGraph([habit, daily], edges=[("nicotine", "d1", "relates_to")])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "nicotine")
        assert st["days_clean"] == 35
        assert st["streak_status"] == "strong"


class TestPeriodicHabitStreaks:
    """Use edge-linked dailies (no vault_root needed)."""

    def test_periodic_habit_next_due(self):
        """Quarterly habit done 10 days ago → next_due in ~80 days."""
        habit = _make_habit("fasting", "Periodic 48-Hour Fasting",
                            kind="periodic", frequency="quarterly")
        last_date = date.today() - timedelta(days=10)
        daily = _make_daily("d1", last_date.isoformat(), "")
        graph = FakeGraph([habit, daily], edges=[("fasting", "d1", "relates_to")])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "fasting")
        assert st["kind"] == "periodic"
        assert st["last_completed"] is not None
        assert st["next_due"] is not None
        assert st["days_until_due"] is not None
        assert 70 <= st["days_until_due"] <= 90
        assert st["streak_status"] == "on_track"
        assert st["current_streak"] is None

    def test_periodic_habit_overdue(self):
        """Quarterly habit done 100 days ago → days_until_due negative, status: overdue."""
        habit = _make_habit("fasting", "Periodic 48-Hour Fasting",
                            kind="periodic", frequency="quarterly")
        last_date = date.today() - timedelta(days=100)
        daily = _make_daily("d1", last_date.isoformat(), "")
        graph = FakeGraph([habit, daily], edges=[("fasting", "d1", "relates_to")])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "fasting")
        assert st["days_until_due"] < 0
        assert st["streak_status"] == "overdue"

    def test_periodic_habit_no_completions(self):
        """Periodic with no linked dailies → status: no_data."""
        habit = _make_habit("fasting", "Periodic 48-Hour Fasting",
                            kind="periodic", frequency="quarterly")
        graph = FakeGraph([habit])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "fasting")
        assert st["streak_status"] == "no_data"
        assert st["last_completed"] is None
        assert st["next_due"] is None

    def test_periodic_habit_upcoming(self):
        """Quarterly habit done 86 days ago → due in 4 days, status: upcoming."""
        habit = _make_habit("fasting", "Periodic 48-Hour Fasting",
                            kind="periodic", frequency="quarterly")
        last_date = date.today() - timedelta(days=86)
        daily = _make_daily("d1", last_date.isoformat(), "")
        graph = FakeGraph([habit, daily], edges=[("fasting", "d1", "relates_to")])
        results = calculate_streaks(graph)
        st = next(r for r in results if r["habit_id"] == "fasting")
        assert st["streak_status"] == "upcoming"
        assert 0 < st["days_until_due"] <= 7


class TestSortingAcrossKinds:
    def test_sorting_broken_build_at_top(self):
        """Broken build habit sorts before on_track habits."""
        build_broken = _make_habit("b1", "Broken Build", kind="build")
        build_ok = _make_habit("b2", "OK Build", kind="build", frequency="weekly")
        today = date.today()
        # b2 has a recent daily → on_track
        d_ok = _make_daily("d_ok", (today - timedelta(days=2)).isoformat(), "")
        graph = FakeGraph(
            [build_broken, build_ok, d_ok],
            edges=[("b2", "d_ok", "relates_to")],
        )
        results = calculate_streaks(graph)
        statuses = [r["streak_status"] for r in results]
        # broken should appear before on_track
        broken_idx = next(i for i, r in enumerate(results) if r["habit_id"] == "b1")
        ok_idx = next(i for i, r in enumerate(results) if r["habit_id"] == "b2")
        assert broken_idx < ok_idx

    def test_sorting_across_kinds(self):
        """Broken build + relapsed break + overdue periodic → all sort to urgency 0."""
        build = _make_habit("b1", "Build Habit", kind="build")  # no dates → broken
        break_habit = _make_habit("br1", "Nicotine", kind="break")
        periodic = _make_habit("p1", "Fasting", kind="periodic", frequency="quarterly")
        on_track = _make_habit("ok1", "OK Habit", kind="build", frequency="daily")

        today = date.today()
        # Break habit edge-linked to today → relapsed
        d_relapse = _make_daily("d_rel", today.isoformat(), "")
        # Periodic edge-linked 100 days ago → overdue
        d_old = _make_daily("d_old", (today - timedelta(days=100)).isoformat(), "")
        # On-track habit done today
        d_ok = _make_daily("d_ok", today.isoformat(), "")

        graph = FakeGraph(
            [build, break_habit, periodic, on_track, d_relapse, d_old, d_ok],
            edges=[
                ("ok1", "d_ok", "relates_to"),
                ("br1", "d_rel", "relates_to"),
                ("p1", "d_old", "relates_to"),
            ],
        )
        results = calculate_streaks(graph)
        urgent = [r for r in results if r["streak_status"] in ("broken", "relapsed", "overdue")]
        not_urgent = [r for r in results if r["streak_status"] in ("on_track",)]
        assert len(urgent) >= 3
        # All urgent ones appear before non-urgent in sorted output
        for u in urgent:
            u_idx = results.index(u)
            for n in not_urgent:
                n_idx = results.index(n)
                assert u_idx < n_idx
