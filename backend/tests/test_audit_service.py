"""Tests for vault audit service."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from services.audit_service import (
    audit_vault,
    _check_stale_statuses,
    _check_orphans,
    _check_broken_wikilinks,
    _check_type_mismatches,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeGraph:
    """Minimal graph stub for audit tests."""

    def __init__(self, nodes: list[dict], edges: list[tuple[str, str]] | None = None):
        self._nodes = {n["id"]: n for n in nodes}
        self._edges = edges or []

    def get_all_nodes(self) -> list[dict]:
        return list(self._nodes.values())

    def get_degree(self, node_id: str) -> int:
        count = 0
        for src, tgt in self._edges:
            if src == node_id or tgt == node_id:
                count += 1
        return count


# ---------------------------------------------------------------------------
# 1. Stale status
# ---------------------------------------------------------------------------


class TestStaleStatuses:
    def test_stale_status_detected(self):
        """Node with past date + active status → flagged."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        nodes = [{"id": "old-task", "status": "active", "date": yesterday}]
        result = _check_stale_statuses(nodes, date.today())
        assert len(result) == 1
        assert result[0]["id"] == "old-task"
        assert result[0]["date_field"] == "date"

    def test_future_date_not_flagged(self):
        """Node with future date + active status → not flagged."""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        nodes = [{"id": "future-task", "status": "active", "due": tomorrow}]
        result = _check_stale_statuses(nodes, date.today())
        assert len(result) == 0

    def test_no_status_skipped(self):
        """Node without status field → not flagged."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        nodes = [{"id": "no-status", "date": yesterday}]
        result = _check_stale_statuses(nodes, date.today())
        assert len(result) == 0

    def test_completed_status_not_flagged(self):
        """Completed status with past date → not flagged (expected)."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        nodes = [{"id": "done-task", "status": "completed", "date": yesterday}]
        result = _check_stale_statuses(nodes, date.today())
        assert len(result) == 0

    def test_planned_status_flagged(self):
        """Planned status with past date → flagged."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        nodes = [{"id": "planned-task", "status": "planned", "deadline": yesterday}]
        result = _check_stale_statuses(nodes, date.today())
        assert len(result) == 1

    def test_date_object_handled(self):
        """Date fields that are already date objects (from YAML) → handled."""
        yesterday = date.today() - timedelta(days=1)
        nodes = [{"id": "date-obj", "status": "active", "date": yesterday}]
        result = _check_stale_statuses(nodes, date.today())
        assert len(result) == 1

    def test_no_date_fields_skipped(self):
        """Node with status but no date fields → not flagged."""
        nodes = [{"id": "no-dates", "status": "active", "type": "goal"}]
        result = _check_stale_statuses(nodes, date.today())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 2. Orphans
# ---------------------------------------------------------------------------


class TestOrphans:
    def test_orphan_detected(self):
        """Node with zero edges → flagged."""
        nodes = [{"id": "lonely", "type": "note", "title": "Lonely Note"}]
        graph = FakeGraph(nodes)
        result = _check_orphans(nodes, graph)
        assert len(result) == 1
        assert result[0]["id"] == "lonely"

    def test_connected_node_not_orphan(self):
        """Node with edges → not flagged."""
        nodes = [
            {"id": "a", "type": "goal", "title": "A"},
            {"id": "b", "type": "goal", "title": "B"},
        ]
        graph = FakeGraph(nodes, edges=[("a", "b")])
        result = _check_orphans(nodes, graph)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 3. Broken wikilinks
# ---------------------------------------------------------------------------


class TestBrokenWikilinks:
    def test_broken_wikilink_detected(self, tmp_path):
        """Node with [[nonexistent]] in markdown → flagged."""
        vault = tmp_path / "vault"
        vault.mkdir()
        node_dir = vault / "Planning" / "Tasks"
        node_dir.mkdir(parents=True)
        (node_dir / "my-task.md").write_text(
            "---\nid: my-task\ntype: task\n---\n\n# My Task\n\n## Related\n- [[nonexistent-node]]\n"
        )

        all_ids = {"my-task"}
        result = _check_broken_wikilinks(str(vault), all_ids)
        assert len(result) == 1
        assert result[0]["source"] == "my-task"
        assert result[0]["target"] == "nonexistent-node"

    def test_valid_wikilink_not_flagged(self, tmp_path):
        """Node with [[existing-node]] → not flagged."""
        vault = tmp_path / "vault"
        vault.mkdir()
        node_dir = vault / "Self" / "Goals"
        node_dir.mkdir(parents=True)
        (node_dir / "my-goal.md").write_text(
            "---\nid: my-goal\ntype: goal\n---\n\n# My Goal\n\n## Related\n- [[other-goal]]\n"
        )

        all_ids = {"my-goal", "other-goal"}
        result = _check_broken_wikilinks(str(vault), all_ids)
        assert len(result) == 0

    def test_skips_backup_and_templates(self, tmp_path):
        """Files in _backup/ and _templates/ → not scanned."""
        vault = tmp_path / "vault"
        vault.mkdir()
        backup = vault / "_backup"
        backup.mkdir()
        (backup / "old.md").write_text(
            "---\nid: old\ntype: goal\n---\n\n[[broken-link]]\n"
        )
        templates = vault / "_templates"
        templates.mkdir()
        (templates / "tmpl.md").write_text(
            "---\nid: tmpl\ntype: goal\n---\n\n[[broken-link]]\n"
        )

        result = _check_broken_wikilinks(str(vault), {"old", "tmpl"})
        assert len(result) == 0


# ---------------------------------------------------------------------------
# 4. Type mismatches
# ---------------------------------------------------------------------------


class TestTypeMismatches:
    def test_mismatch_detected(self):
        """Node in wrong folder → flagged."""
        nodes = [{"id": "misplaced", "type": "goal", "filepath": "Knowledge/Notes/misplaced.md"}]
        schema = {"types": {"goal": {"folder": "Self/Goals"}}}
        result = _check_type_mismatches(nodes, schema)
        assert len(result) == 1
        assert result[0]["expected_folder"] == "Self/Goals"

    def test_correct_folder_not_flagged(self):
        """Node in right folder → not flagged."""
        nodes = [{"id": "correct", "type": "goal", "filepath": "Self/Goals/correct.md"}]
        schema = {"types": {"goal": {"folder": "Self/Goals"}}}
        result = _check_type_mismatches(nodes, schema)
        assert len(result) == 0

    def test_unknown_type_flagged(self):
        """Node with type not in schema → flagged with expected_folder=None."""
        nodes = [{"id": "weird", "type": "foobar", "filepath": "Misc/weird.md"}]
        schema = {"types": {"goal": {"folder": "Self/Goals"}}}
        result = _check_type_mismatches(nodes, schema)
        assert len(result) == 1
        assert result[0]["expected_folder"] is None


# ---------------------------------------------------------------------------
# 5. Full audit (integration)
# ---------------------------------------------------------------------------


class TestAuditIntegration:
    def test_empty_vault_zero_issues(self, tmp_path):
        """Empty vault returns zero issues."""
        vault = tmp_path / "vault"
        vault.mkdir()
        graph = FakeGraph([])
        schema = {"types": {}}

        result = audit_vault(graph, schema, str(vault))
        assert result["total_nodes"] == 0
        assert result["summary"]["stale_status"] == 0
        assert result["summary"]["orphans"] == 0
        assert result["summary"]["broken_wikilinks"] == 0
        assert result["summary"]["type_mismatches"] == 0

    def test_full_audit_with_test_vault(self, tmp_vault, graph, schema):
        """Run audit against the standard test vault fixture."""
        result = audit_vault(graph, schema, str(tmp_vault))
        assert result["total_nodes"] == 4
        assert isinstance(result["issues"], dict)
        assert isinstance(result["summary"], dict)
        # All 4 issue categories present
        for key in ("stale_status", "orphans", "broken_wikilinks", "type_mismatches"):
            assert key in result["issues"]
            assert key in result["summary"]
