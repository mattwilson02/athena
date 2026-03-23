"""Tests for VectorIndex with metadata filtering and build_search_filter."""

from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vector_search import VectorIndex, build_search_filter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def index(tmp_path):
    """Fresh VectorIndex backed by a temp directory."""
    return VectorIndex(str(tmp_path / "chroma"))


@pytest.fixture
def populated_index(index):
    """VectorIndex with a variety of node types and statuses."""
    nodes = [
        {
            "id": "goal-1",
            "type": "goal",
            "title": "Learn Piano",
            "content": "I want to learn piano",
            "status": "active",
            "domain": "Self",
        },
        {
            "id": "goal-2",
            "type": "goal",
            "title": "Run a Marathon",
            "content": "Train for running",
            "status": "completed",
            "domain": "Self",
        },
        {
            "id": "task-1",
            "type": "task",
            "title": "Book flights",
            "content": "Book flights for Italy",
            "status": "active",
            "domain": "Planning",
        },
        {
            "id": "note-1",
            "type": "note",
            "title": "Music Theory",
            "content": "Scales and chords",
            "status": "",
            "domain": "Knowledge",
        },
        {
            "id": "habit-1",
            "type": "habit",
            "title": "Morning Meditation",
            "content": "Daily meditation practice",
            "status": "cancelled",
            "domain": "Self",
        },
    ]
    index.index_all(nodes)
    return index


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestSearchBackwardCompat:

    def test_search_no_filter_backward_compat(self, populated_index):
        """search() without where returns results — identical to pre-sprint behaviour."""
        results = populated_index.search("piano", n=5)
        assert len(results) > 0
        assert all("id" in r for r in results)

    def test_search_returns_dicts_with_expected_keys(self, populated_index):
        results = populated_index.search("music", n=3)
        for r in results:
            assert "id" in r
            assert "score" in r
            assert "title" in r
            assert "type" in r


# ---------------------------------------------------------------------------
# Type filtering
# ---------------------------------------------------------------------------

class TestTypeFilter:

    def test_search_type_filter(self, populated_index):
        """Filter to goals only — no tasks or notes in results."""
        where = build_search_filter(types=["goal"])
        results = populated_index.search("learn", n=10, where=where)
        assert len(results) > 0
        for r in results:
            assert r["type"] == "goal", f"Expected goal, got {r['type']}"

    def test_search_type_filter_multiple_types(self, populated_index):
        where = build_search_filter(types=["goal", "task"])
        results = populated_index.search("active work", n=10, where=where)
        for r in results:
            assert r["type"] in ("goal", "task")


# ---------------------------------------------------------------------------
# Status exclusion
# ---------------------------------------------------------------------------

class TestStatusFilter:

    def test_search_status_exclusion(self, populated_index):
        """Nodes with excluded statuses should not appear in results."""
        where = build_search_filter(exclude_statuses=["completed", "cancelled"])
        results = populated_index.search("goal", n=10, where=where)
        result_ids = [r["id"] for r in results]
        assert "goal-2" not in result_ids   # completed
        assert "habit-1" not in result_ids  # cancelled


# ---------------------------------------------------------------------------
# Compound filter
# ---------------------------------------------------------------------------

class TestCompoundFilter:

    def test_search_compound_filter(self, populated_index):
        """Type + status filter together — goals that are not completed."""
        where = build_search_filter(types=["goal"], exclude_statuses=["completed"])
        results = populated_index.search("goal", n=10, where=where)
        for r in results:
            assert r["type"] == "goal"
            assert r["id"] != "goal-2"  # completed goal excluded


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestSearchEdgeCases:

    def test_search_filter_no_matches(self, populated_index):
        """Filter that matches nothing → empty list (not an error)."""
        where = build_search_filter(types=["nonexistent_type"])
        results = populated_index.search("anything", n=5, where=where)
        assert results == []

    def test_search_invalid_filter_fallback(self, populated_index, caplog):
        """Malformed where clause → falls back to unfiltered, logs warning."""
        # An unsupported operator should cause ChromaDB to raise
        bad_filter = {"$badop": [{"type": "goal"}]}
        with caplog.at_level(logging.WARNING):
            results = populated_index.search("piano", n=5, where=bad_filter)
        # Should still return results from unfiltered fallback
        assert len(results) > 0
        assert any("filter" in record.message.lower() or "where" in record.message.lower()
                   for record in caplog.records)

    def test_search_none_where_is_unfiltered(self, populated_index):
        results_no_filter = populated_index.search("goal", n=10)
        results_none = populated_index.search("goal", n=10, where=None)
        assert len(results_no_filter) == len(results_none)


# ---------------------------------------------------------------------------
# build_search_filter
# ---------------------------------------------------------------------------

class TestBuildSearchFilter:

    def test_build_search_filter_types_only(self):
        result = build_search_filter(types=["goal", "habit"])
        assert result == {"type": {"$in": ["goal", "habit"]}}

    def test_build_search_filter_statuses_only(self):
        result = build_search_filter(exclude_statuses=["completed", "cancelled"])
        assert result == {"status": {"$nin": ["completed", "cancelled"]}}

    def test_build_search_filter_combined(self):
        result = build_search_filter(types=["goal"], exclude_statuses=["completed"])
        assert result == {
            "$and": [
                {"type": {"$in": ["goal"]}},
                {"status": {"$nin": ["completed"]}},
            ]
        }

    def test_build_search_filter_none(self):
        result = build_search_filter()
        assert result is None

    def test_build_search_filter_empty_lists(self):
        result = build_search_filter(types=[], exclude_statuses=[])
        assert result is None


# ---------------------------------------------------------------------------
# Metadata fields
# ---------------------------------------------------------------------------

class TestMetadataFields:

    def test_metadata_includes_status(self, index):
        node = {"id": "test-1", "type": "goal", "title": "Test Goal", "status": "active"}
        index.index_all([node])
        # Verify by searching and checking ChromaDB internal collection
        raw = index.collection.get(ids=["test-1"], include=["metadatas"])
        assert raw["metadatas"][0]["status"] == "active"

    def test_metadata_includes_domain(self, index):
        node = {"id": "test-2", "type": "goal", "title": "Test", "domain": "Self", "status": ""}
        index.index_all([node])
        raw = index.collection.get(ids=["test-2"], include=["metadatas"])
        assert raw["metadatas"][0]["domain"] == "Self"

    def test_metadata_domain_absent_when_not_set(self, index):
        node = {"id": "test-3", "type": "note", "title": "No Domain", "status": ""}
        index.index_all([node])
        raw = index.collection.get(ids=["test-3"], include=["metadatas"])
        assert "domain" not in raw["metadatas"][0]
