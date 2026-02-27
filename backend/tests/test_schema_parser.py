"""Tests for schema_parser.py — schema.md parsing."""

from __future__ import annotations

from schema_parser import parse_schema, get_folder_for_type, get_edge_map, generate_type_rules


class TestParseDomains:

    def test_parse_domains(self, schema):
        assert "Self" in schema["domains"]
        assert "People" in schema["domains"]
        assert "Knowledge" in schema["domains"]
        assert len(schema["domains"]) == 3

    def test_domain_has_folder(self, schema):
        assert schema["domains"]["Self"]["folder"] == "Self"

    def test_domain_has_description(self, schema):
        assert "Inner world" in schema["domains"]["Self"]["description"]


class TestParseTypes:

    def test_parse_types(self, schema):
        assert "goal" in schema["types"]
        assert "fear" in schema["types"]
        assert "person" in schema["types"]
        assert "note" in schema["types"]
        assert len(schema["types"]) == 4

    def test_type_has_domain(self, schema):
        assert schema["types"]["goal"]["domain"] == "Self"
        assert schema["types"]["person"]["domain"] == "People"

    def test_type_has_folder(self, schema):
        assert schema["types"]["goal"]["folder"] == "Self/Goals"
        assert schema["types"]["person"]["folder"] == "People/Persons"

    def test_parse_frontmatter_fields(self, schema):
        goal_fm = schema["types"]["goal"]["frontmatter"]
        assert "status" in goal_fm
        assert "priority" in goal_fm

    def test_type_list(self, schema):
        assert sorted(schema["type_list"]) == ["fear", "goal", "note", "person"]

    def test_domain_list(self, schema):
        assert sorted(schema["domain_list"]) == ["Knowledge", "People", "Self"]


class TestParseEdges:

    def test_parse_edge_types(self, schema):
        edges = schema["edges"]
        assert edges["blockers"] == "blocked_by"
        assert edges["supports"] == "supported_by"
        assert edges["related"] == "relates_to"
        assert edges["people"] == "involves"
        assert edges["part of"] == "part_of"

    def test_default_edge(self, schema):
        """Links outside sections map to relates_to."""
        assert schema["edges"].get("_default") == "relates_to"


class TestHelpers:

    def test_get_folder_for_type(self, schema):
        assert get_folder_for_type(schema, "goal") == "Self/Goals"
        assert get_folder_for_type(schema, "person") == "People/Persons"
        assert get_folder_for_type(schema, "nonexistent") is None

    def test_get_edge_map_excludes_default(self, schema):
        edge_map = get_edge_map(schema)
        assert "_default" not in edge_map
        assert "blockers" in edge_map

    def test_generate_type_rules(self, schema):
        rules = generate_type_rules(schema)
        assert "Self" in rules
        assert "goal" in rules
        assert "person" in rules

    def test_types_crossreferenced_to_domains(self, schema):
        """Types are listed under their domain."""
        self_types = schema["domains"]["Self"].get("types", [])
        assert "goal" in self_types
        assert "fear" in self_types
