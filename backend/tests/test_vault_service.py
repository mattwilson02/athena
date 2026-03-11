"""Tests for services/vault_service.py — write, update, and helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
import yaml

from services.vault_service import (
    VaultService,
    _sanitize_id,
    _split_frontmatter,
    _edge_type_to_section,
    _dedup_sections,
    _add_wikilink_to_section,
    _infer_edge_type,
    _suggest_type,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(tmp_vault, schema, graph):
    """Create a VaultService with a mock vector_index and a real rebuild_fn."""
    from vault_parser import VaultParser
    from schema_parser import get_edge_map

    parser = VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))
    vector_index = MagicMock()
    vector_index.rebuild = MagicMock()
    vector_index.search = MagicMock(return_value=[])
    vector_index.find_duplicates = MagicMock(return_value=[])

    def rebuild():
        nodes, edges = parser.parse()
        graph.build_from_parsed(nodes, edges)
        return graph.get_stats()

    rebuild()  # initial build
    return VaultService(str(tmp_vault), graph, vector_index, schema, rebuild)


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------

class TestSanitizeId:

    def test_basic(self):
        assert _sanitize_id("My Goal") == "my-goal"

    def test_underscores_to_hyphens(self):
        assert _sanitize_id("my_goal_here") == "my-goal-here"

    def test_special_chars_removed(self):
        assert _sanitize_id("café & résumé!") == "caf-rsum"

    def test_consecutive_hyphens_collapsed(self):
        assert _sanitize_id("a---b") == "a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert _sanitize_id("-my-id-") == "my-id"


class TestSplitFrontmatter:

    def test_valid_frontmatter(self):
        fm, body = _split_frontmatter("---\nid: test\ntype: goal\n---\n\nBody here.")
        assert fm["id"] == "test"
        assert fm["type"] == "goal"
        assert "Body here." in body

    def test_no_frontmatter(self):
        fm, body = _split_frontmatter("Just plain text.")
        assert fm == {}
        assert body == "Just plain text."

    def test_malformed_no_closing(self):
        fm, body = _split_frontmatter("---\nid: bad\nNo closing fence")
        assert fm == {}


class TestEdgeTypeToSection:

    def test_known_types(self):
        assert _edge_type_to_section("blocked_by") == "Blockers"
        assert _edge_type_to_section("involves") == "People"
        assert _edge_type_to_section("part_of") == "Part Of"

    def test_unknown_defaults_to_related(self):
        assert _edge_type_to_section("unknown_edge") == "Related"


class TestDedupSections:

    def test_merges_duplicate_sections(self):
        body = "# Title\n\nContent\n\n## Related\n- [[a]]\n\n## Related\n- [[b]]\n"
        result = _dedup_sections(body)
        assert result.count("## Related") == 1
        assert "[[a]]" in result
        assert "[[b]]" in result

    def test_no_duplicates_unchanged(self):
        body = "# Title\n\nContent\n\n## Related\n- [[a]]\n\n## People\n- [[b]]\n"
        result = _dedup_sections(body)
        assert result.count("## Related") == 1
        assert result.count("## People") == 1

    def test_dedup_preserves_existing_links(self):
        body = "## Related\n- [[a]]\n\n## Related\n- [[a]]\n- [[b]]\n"
        result = _dedup_sections(body)
        # [[a]] should appear once, [[b]] should be kept
        assert result.count("[[a]]") == 1
        assert "[[b]]" in result


class TestAddWikilinkToSection:

    def test_adds_to_existing_section(self):
        body = "# Title\n\n## Related\n- [[a]]\n"
        result = _add_wikilink_to_section(body, "Related", "b")
        assert "[[b]]" in result
        assert result.count("## Related") == 1

    def test_creates_section_if_missing(self):
        body = "# Title\n\nSome content.\n"
        result = _add_wikilink_to_section(body, "People", "alice")
        assert "## People" in result
        assert "[[alice]]" in result

    def test_no_duplicate_wikilink(self):
        body = "# Title\n\n## Related\n- [[a]]\n"
        result = _add_wikilink_to_section(body, "Related", "a")
        assert result.count("[[a]]") == 1


class TestInferEdgeType:

    def test_person_involvement(self):
        assert _infer_edge_type("goal", "person") == "involves"

    def test_place_location(self):
        assert _infer_edge_type("event", "place") == "located_in"

    def test_project_part_of(self):
        assert _infer_edge_type("task", "project") == "part_of"

    def test_default_relates(self):
        assert _infer_edge_type("goal", "goal") == "relates_to"


# ---------------------------------------------------------------------------
# Write tests
# ---------------------------------------------------------------------------

class TestVaultServiceWrite:

    def test_write_creates_file_in_correct_folder(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.write({
            "node_id": "new-goal",
            "title": "New Goal",
            "type": "goal",
            "content": "A brand new goal.",
        })
        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "new-goal.md"
        assert filepath.exists()

    def test_write_generates_valid_frontmatter(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        svc.write({
            "node_id": "fm-test",
            "title": "FM Test",
            "type": "note",
            "content": "Testing frontmatter.",
        })
        filepath = tmp_vault / "Knowledge" / "Notes" / "fm-test.md"
        raw = filepath.read_text()
        assert raw.startswith("---\n")
        fm, _ = _split_frontmatter(raw)
        assert fm["id"] == "fm-test"
        assert fm["type"] == "note"
        assert fm["title"] == "FM Test"

    def test_write_sanitises_id(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.write({
            "node_id": "My_New Goal!",
            "title": "My New Goal",
            "type": "goal",
            "content": "test",
        })
        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "my-new-goal.md"
        assert filepath.exists()

    def test_write_rejects_invalid_type(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.write({
            "node_id": "bad-type",
            "title": "Bad",
            "type": "unicorn",
            "content": "test",
        })
        assert "error" in result
        assert result["status"] == 400

    def test_write_adds_wikilinks_under_sections(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        svc.write({
            "node_id": "edged-goal",
            "title": "Edged Goal",
            "type": "goal",
            "content": "With edges.",
            "edges": [
                {"target": "alice", "type": "involves"},
                {"target": "learn-piano", "type": "relates_to"},
            ],
        })
        filepath = tmp_vault / "Self" / "Goals" / "edged-goal.md"
        raw = filepath.read_text()
        assert "## People" in raw
        assert "[[alice]]" in raw
        assert "## Related" in raw
        assert "[[learn-piano]]" in raw

    def test_write_requires_node_id(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.write({"title": "No ID", "type": "goal"})
        assert "error" in result


class TestVaultServiceUpdate:

    def test_update_title(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.update({
            "node_id": "learn-piano",
            "changes": {"title": "Master Piano"},
        })
        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "learn-piano.md"
        raw = filepath.read_text()
        fm, body = _split_frontmatter(raw)
        assert fm["title"] == "Master Piano"
        assert "# Master Piano" in body

    def test_update_content(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.update({
            "node_id": "time-management",
            "changes": {"content": "Updated content here."},
        })
        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "time-management.md"
        raw = filepath.read_text()
        assert "Updated content here." in raw

    def test_update_add_tags(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.update({
            "node_id": "learn-piano",
            "changes": {"add_tags": ["skills"]},
        })
        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "learn-piano.md"
        raw = filepath.read_text()
        fm, _ = _split_frontmatter(raw)
        assert "skills" in fm["tags"]
        assert "music" in fm["tags"]  # original tag preserved

    def test_update_remove_tags(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.update({
            "node_id": "learn-piano",
            "changes": {"remove_tags": ["learning"]},
        })
        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "learn-piano.md"
        raw = filepath.read_text()
        fm, _ = _split_frontmatter(raw)
        assert "learning" not in fm["tags"]
        assert "music" in fm["tags"]

    def test_update_add_edge(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.update({
            "node_id": "time-management",
            "changes": {"add_edges": [{"target": "alice", "type": "involves"}]},
        })
        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "time-management.md"
        raw = filepath.read_text()
        assert "## People" in raw
        assert "[[alice]]" in raw

    def test_update_frontmatter_field(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.update({
            "node_id": "learn-piano",
            "changes": {"frontmatter": {"status": "paused"}},
        })
        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "learn-piano.md"
        raw = filepath.read_text()
        fm, _ = _split_frontmatter(raw)
        assert fm["status"] == "paused"

    def test_update_nonexistent_node(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.update({
            "node_id": "doesnt-exist",
            "changes": {"title": "Nope"},
        })
        assert "error" in result
        assert result["status"] == 404


class TestWriteRoundtrip:

    def test_write_then_parse_roundtrip(self, tmp_vault, schema, graph):
        """Write a node → parse vault → all fields match."""
        svc = _make_service(tmp_vault, schema, graph)
        svc.write({
            "node_id": "roundtrip-test",
            "title": "Roundtrip Test",
            "type": "note",
            "content": "Testing the roundtrip.",
            "frontmatter": {
                "tags": ["test", "roundtrip"],
            },
            "edges": [{"target": "alice", "type": "involves"}],
        })

        from vault_parser import VaultParser
        from schema_parser import get_edge_map
        p = VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))
        nodes, edges = p.parse()

        node = next(n for n in nodes if n["id"] == "roundtrip-test")
        assert node["title"] == "Roundtrip Test"
        assert node["type"] == "note"
        assert "Testing the roundtrip." in node["content"]

        node_edges = [e for e in edges if e[0] == "roundtrip-test"]
        assert any(e[1] == "alice" and e[2] == "involves" for e in node_edges)


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------


class TestSuggestType:

    def test_known_remap(self):
        valid = {"pill", "movie", "person", "goal", "note"}
        assert _suggest_type("lesson", valid) == "pill"
        assert _suggest_type("film", valid) == "movie"
        assert _suggest_type("contact", valid) == "person"
        assert _suggest_type("aspiration", valid) == "goal"

    def test_unknown_falls_back_to_note(self):
        valid = {"goal", "note"}
        assert _suggest_type("widget", valid) == "note"

    def test_case_insensitive(self):
        valid = {"pill", "note"}
        assert _suggest_type("LESSON", valid) == "pill"


class TestImportProposals:

    def _setup_backup(self, tmp_vault):
        """Create backup files for import testing."""
        backup = tmp_vault / "_backup" / "v1"
        backup.mkdir(parents=True, exist_ok=True)

        # Valid node — should be "ready"
        (backup / "cape-town.md").write_text(
            "---\nid: cape-town\ntype: note\ntitle: Cape Town\n"
            "tags:\n  - travel\n---\n\n# Cape Town\n\nGreat city.\n"
        )

        # Duplicate node — same ID as existing "alice"
        (backup / "alice.md").write_text(
            "---\nid: alice\ntype: person\ntitle: Alice Duplicate\n---\n\n# Alice Duplicate\n"
        )

        # Invalid type — should be "needs_fix"
        (backup / "lesson-node.md").write_text(
            "---\nid: lesson-node\ntype: lesson\ntitle: Life Lesson\n---\n\n# Life Lesson\n\nSomething learned.\n"
        )

        return backup

    def test_proposals_returns_all_nodes(self, tmp_vault, schema, graph):
        self._setup_backup(tmp_vault)
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_proposals("_backup/v1")

        assert result["summary"]["total"] == 3
        ids = {p["node_id"] for p in result["proposals"]}
        assert "cape-town" in ids
        assert "alice" in ids
        assert "lesson-node" in ids

    def test_valid_node_is_ready(self, tmp_vault, schema, graph):
        self._setup_backup(tmp_vault)
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_proposals("_backup/v1")

        cape_town = next(p for p in result["proposals"] if p["node_id"] == "cape-town")
        assert cape_town["status"] == "ready"
        assert cape_town["type_valid"] is True
        assert cape_town["duplicate"] is None

    def test_duplicate_node_flagged(self, tmp_vault, schema, graph):
        self._setup_backup(tmp_vault)
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_proposals("_backup/v1")

        alice = next(p for p in result["proposals"] if p["node_id"] == "alice")
        assert alice["status"] == "duplicate"
        assert alice["duplicate"]["match"] == "exact_id"

    def test_invalid_type_flagged(self, tmp_vault, schema, graph):
        self._setup_backup(tmp_vault)
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_proposals("_backup/v1")

        lesson = next(p for p in result["proposals"] if p["node_id"] == "lesson-node")
        assert lesson["status"] == "needs_fix"
        assert lesson["type_valid"] is False
        # "lesson" isn't in the test schema's type_list, so suggested_type should be "note" (fallback)
        assert lesson["suggested_type"] is not None

    def test_missing_source_dir_returns_empty(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_proposals("_backup/nonexistent")
        assert result["summary"]["total"] == 0

    def test_summary_counts(self, tmp_vault, schema, graph):
        self._setup_backup(tmp_vault)
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_proposals("_backup/v1")

        assert result["summary"]["ready"] == 1
        assert result["summary"]["duplicate"] == 1
        assert result["summary"]["needs_fix"] == 1


class TestImportAccept:

    def _setup_backup(self, tmp_vault):
        backup = tmp_vault / "_backup" / "v1"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "cape-town.md").write_text(
            "---\nid: cape-town\ntype: note\ntitle: Cape Town\n"
            "tags:\n  - travel\n---\n\n# Cape Town\n\nGreat city.\n"
        )
        return backup

    def test_accept_writes_node(self, tmp_vault, schema, graph):
        self._setup_backup(tmp_vault)
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_accept("cape-town", "_backup/v1")

        assert result.get("ok") is True
        assert (tmp_vault / "Knowledge" / "Notes" / "cape-town.md").exists()

    def test_accept_with_type_override(self, tmp_vault, schema, graph):
        self._setup_backup(tmp_vault)
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_accept("cape-town", "_backup/v1", type_override="goal")

        assert result.get("ok") is True
        filepath = tmp_vault / "Self" / "Goals" / "cape-town.md"
        assert filepath.exists()

    def test_accept_nonexistent_node(self, tmp_vault, schema, graph):
        self._setup_backup(tmp_vault)
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_accept("nonexistent", "_backup/v1")

        assert "error" in result
        assert result["status"] == 404

    def test_accept_nonexistent_source_dir(self, tmp_vault, schema, graph):
        svc = _make_service(tmp_vault, schema, graph)
        result = svc.import_accept("anything", "_backup/nonexistent")

        assert "error" in result
        assert result["status"] == 404
