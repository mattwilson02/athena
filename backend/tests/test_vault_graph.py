"""Tests for vault_graph.py — NetworkX graph operations."""

from __future__ import annotations

from vault_graph import VaultGraph


class TestBuild:
    """Verify graph construction from parsed data."""

    def test_build_from_parsed_nodes_and_edges(self, graph, parsed):
        nodes, edges = parsed
        assert graph.graph.number_of_nodes() == len(nodes)
        # Only valid edges (no dangling targets) should be added
        assert graph.graph.number_of_edges() >= 1

    def test_dangling_edge_skipped(self):
        """Edge to nonexistent node → skipped, not crash."""
        g = VaultGraph()
        nodes = [{"id": "a", "type": "goal", "title": "A"}]
        edges = [("a", "nonexistent", "relates_to")]
        g.build_from_parsed(nodes, edges)
        assert g.graph.number_of_nodes() == 1
        assert g.graph.number_of_edges() == 0


class TestNodeAccess:
    """Verify node lookup and field access."""

    def test_get_node_returns_all_fields(self, graph):
        node = graph.get_node("learn-piano")
        assert node is not None
        assert node["id"] == "learn-piano"
        assert node["type"] == "goal"
        assert node["title"] == "Learn Piano"
        assert "content" in node
        assert "tags" in node

    def test_get_node_not_found(self, graph):
        assert graph.get_node("nonexistent") is None

    def test_get_nodes_by_type(self, graph):
        goals = graph.get_nodes_by_type("goal")
        assert len(goals) == 2
        ids = {n["id"] for n in goals}
        assert "learn-piano" in ids
        assert "time-management" in ids

    def test_get_nodes_by_type_empty(self, graph):
        assert graph.get_nodes_by_type("project") == []


class TestNeighbors:
    """Verify neighbor traversal."""

    def test_get_neighbors_returns_connected_nodes(self, graph):
        neighbors = graph.get_neighbors("learn-piano")
        neighbor_ids = {n["id"] for n in neighbors}
        # learn-piano -> time-management (blocked_by), learn-piano -> music-theory (relates_to)
        assert "time-management" in neighbor_ids
        assert "music-theory" in neighbor_ids

    def test_get_neighbors_with_edges(self, graph):
        neighbors = graph.get_neighbors_with_edges("learn-piano")
        assert len(neighbors) >= 2
        # Check that edge metadata is present
        for n in neighbors:
            assert "_edge_type" in n
            assert "_edge_direction" in n
            assert n["_edge_direction"] in ("outgoing", "incoming")

    def test_get_neighbors_by_hop(self, graph):
        """2-hop traversal returns correct nodes at each tier."""
        by_hop = graph.get_neighbors_by_hop("learn-piano", depth=2)
        # 1-hop: time-management, music-theory
        hop1_ids = {n["id"] for n in by_hop.get(1, [])}
        assert "time-management" in hop1_ids
        assert "music-theory" in hop1_ids
        # 2-hop: alice (music-theory -> alice via involves)
        hop2_ids = {n["id"] for n in by_hop.get(2, [])}
        assert "alice" in hop2_ids

    def test_get_neighbors_nonexistent_node(self, graph):
        assert graph.get_neighbors("nope") == []
        assert graph.get_neighbors_by_hop("nope") == {}


class TestStats:
    """Verify graph statistics."""

    def test_get_stats(self, graph):
        stats = graph.get_stats()
        assert stats["total_nodes"] == 4
        assert stats["total_edges"] >= 3
        assert stats["types"]["goal"] == 2
        assert stats["types"]["note"] == 1
        assert stats["types"]["person"] == 1
