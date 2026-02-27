"""Tests for vault_parser.py — markdown → nodes + edges."""

from __future__ import annotations

import os

import pytest

from vault_parser import VaultParser, DEFAULT_EDGE_MAP


class TestParseNodes:
    """Verify nodes are extracted with correct fields."""

    def test_parse_node_with_full_frontmatter(self, parsed):
        nodes, _ = parsed
        node = next(n for n in nodes if n["id"] == "learn-piano")
        assert node["type"] == "goal"
        assert node["title"] == "Learn Piano"
        assert str(node["created"]) == "2026-01-15"
        assert str(node["updated"]) == "2026-02-20"
        assert node["tags"] == ["music", "learning"]
        assert node["status"] == "active"
        assert node["priority"] == "high"

    def test_parse_node_minimal_frontmatter(self, tmp_vault, schema):
        """Node with only id/type/title still parses."""
        minimal = tmp_vault / "Knowledge" / "Notes" / "minimal.md"
        minimal.parent.mkdir(parents=True, exist_ok=True)
        minimal.write_text("---\nid: minimal\ntype: note\ntitle: Minimal\n---\n\nJust a note.\n")

        from schema_parser import get_edge_map
        p = VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))
        nodes, _ = p.parse()

        node = next(n for n in nodes if n["id"] == "minimal")
        assert node["title"] == "Minimal"
        assert node["type"] == "note"
        assert node["content"] == "Just a note."

    def test_parse_content_extraction(self, parsed):
        nodes, _ = parsed
        node = next(n for n in nodes if n["id"] == "time-management")
        assert "Getting better at managing time" in node["content"]

    def test_parse_strips_leading_newlines(self, tmp_vault, schema):
        """Leading newlines between frontmatter and body are stripped (V3 fix)."""
        path = tmp_vault / "Knowledge" / "Notes" / "newlines.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nid: newlines\ntype: note\ntitle: NL\n---\n\n\n\nBody here.\n")

        from schema_parser import get_edge_map
        p = VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))
        nodes, _ = p.parse()

        node = next(n for n in nodes if n["id"] == "newlines")
        assert node["content"].startswith("Body here.")

    def test_parse_handles_empty_file(self, tmp_vault, schema):
        """Empty .md file doesn't crash parser."""
        empty = tmp_vault / "Self" / "Goals" / "empty.md"
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_text("")

        from schema_parser import get_edge_map
        p = VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))
        nodes, _ = p.parse()
        # Empty file should be skipped — no node with empty id
        assert not any(n["id"] == "" for n in nodes)

    def test_parse_handles_malformed_frontmatter(self, tmp_vault, schema):
        """Missing closing --- delimiters → file skipped gracefully."""
        bad = tmp_vault / "Knowledge" / "Notes" / "bad-fm.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("---\nid: bad-fm\ntype: note\ntitle: Bad\nNo closing fence here\n")

        from schema_parser import get_edge_map
        p = VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))
        nodes, _ = p.parse()
        # Should not crash; the file might be skipped (no valid frontmatter → no id)
        assert not any(n["id"] == "bad-fm" for n in nodes)

    def test_parse_node_count(self, parsed):
        """Correct number of nodes parsed from test vault."""
        nodes, _ = parsed
        assert len(nodes) == 4


class TestParseEdges:
    """Verify wikilinks are extracted with correct edge types."""

    def test_parse_wikilinks_under_sections(self, parsed):
        """[[target]] under ## Blockers → edge type blocked_by."""
        _, edges = parsed
        blockers = [e for e in edges if e[0] == "learn-piano" and e[2] == "blocked_by"]
        assert len(blockers) == 1
        assert blockers[0][1] == "time-management"

    def test_parse_wikilinks_default_section(self, tmp_vault, schema):
        """[[target]] outside any section → edge type relates_to."""
        loose = tmp_vault / "Knowledge" / "Notes" / "loose.md"
        loose.parent.mkdir(parents=True, exist_ok=True)
        loose.write_text(
            "---\nid: loose\ntype: note\ntitle: Loose\n---\n\n"
            "Some text mentioning [[alice]] with no section heading.\n"
        )

        from schema_parser import get_edge_map
        p = VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))
        _, edges = p.parse()

        loose_edges = [e for e in edges if e[0] == "loose"]
        assert len(loose_edges) == 1
        assert loose_edges[0][1] == "alice"
        assert loose_edges[0][2] == "relates_to"

    def test_parse_multiple_edge_sections(self, parsed):
        """Node with ## Related + ## People → different edge types."""
        _, edges = parsed
        piano_edges = [e for e in edges if e[0] == "learn-piano"]
        edge_types = {e[2] for e in piano_edges}
        assert "blocked_by" in edge_types
        assert "relates_to" in edge_types

    def test_parse_people_section(self, parsed):
        """## People → involves edge type."""
        _, edges = parsed
        people_edges = [e for e in edges if e[0] == "music-theory" and e[2] == "involves"]
        assert len(people_edges) == 1
        assert people_edges[0][1] == "alice"


class TestSkipDirs:
    """Verify special directories are ignored."""

    def test_parse_ignores_backup_dir(self, parsed):
        nodes, _ = parsed
        assert not any(n["id"] == "old" for n in nodes)

    def test_parse_ignores_templates_dir(self, parsed):
        nodes, _ = parsed
        assert not any(n["id"] == "template" for n in nodes)

    def test_parse_ignores_meta_dir(self, parsed):
        nodes, _ = parsed
        assert not any(n["id"] == "schema" for n in nodes)
