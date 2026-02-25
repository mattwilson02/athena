"""In-memory directed graph wrapping NetworkX DiGraph."""

from __future__ import annotations

import logging
from collections import Counter

import networkx as nx

logger = logging.getLogger(__name__)


class VaultGraph:
    """Directed graph of vault nodes with typed edges."""

    def __init__(self) -> None:
        self.graph = nx.DiGraph()

    def build_from_parsed(
        self, nodes: list[dict], edges: list[tuple[str, str, str]]
    ) -> None:
        """Clear graph and rebuild from parsed vault data."""
        self.graph.clear()

        for node in nodes:
            self.graph.add_node(node["id"], **node)

        for source, target, edge_type in edges:
            if source not in self.graph:
                logger.warning(f"Edge source not found, skipping: {source}")
                continue
            if target not in self.graph:
                logger.warning(f"Dangling edge target '{target}' from '{source}', skipping")
                continue
            self.graph.add_edge(source, target, type=edge_type)

        logger.info(
            f"Graph built: {self.graph.number_of_nodes()} nodes, "
            f"{self.graph.number_of_edges()} edges"
        )

    def get_node(self, node_id: str) -> dict | None:
        """Return node attributes dict, or None if not found."""
        if node_id not in self.graph:
            return None
        data = dict(self.graph.nodes[node_id])
        data["id"] = node_id
        return data

    def get_neighbors(self, node_id: str, depth: int = 1) -> list[dict]:
        """Return nodes within `depth` hops (both directions)."""
        if node_id not in self.graph:
            return []

        undirected = self.graph.to_undirected()
        ego = nx.ego_graph(undirected, node_id, radius=depth)
        neighbors = []
        for nid in ego.nodes:
            if nid == node_id:
                continue
            data = dict(self.graph.nodes[nid])
            data["id"] = nid
            neighbors.append(data)
        return neighbors

    def get_nodes_by_type(self, node_type: str) -> list[dict]:
        """Return all nodes of a given type."""
        results = []
        for nid, data in self.graph.nodes(data=True):
            if data.get("type") == node_type:
                node = dict(data)
                node["id"] = nid
                results.append(node)
        return results

    def get_all_nodes(self) -> list[dict]:
        """Return all nodes as a list of dicts."""
        results = []
        for nid, data in self.graph.nodes(data=True):
            node = dict(data)
            node["id"] = nid
            results.append(node)
        return results

    def get_all_edges(self) -> list[dict]:
        """Return all edges as a list of dicts."""
        results = []
        for source, target, data in self.graph.edges(data=True):
            results.append({
                "source": source,
                "target": target,
                "type": data.get("type", "relates_to"),
            })
        return results

    def get_degree(self, node_id: str) -> int:
        """Return total degree (in + out) for a node."""
        if node_id not in self.graph:
            return 0
        return self.graph.in_degree(node_id) + self.graph.out_degree(node_id)

    def get_neighbors_by_hop(self, node_id: str, depth: int = 2) -> dict[int, list[dict]]:
        """Return neighbors grouped by hop distance.

        Returns {1: [nodes at 1-hop], 2: [nodes at 2-hop], ...}.
        """
        if node_id not in self.graph:
            return {}

        undirected = self.graph.to_undirected()
        distances = nx.single_source_shortest_path_length(undirected, node_id, cutoff=depth)

        by_hop: dict[int, list[dict]] = {}
        for nid, dist in distances.items():
            if nid == node_id or dist == 0:
                continue
            data = dict(self.graph.nodes[nid])
            data["id"] = nid
            by_hop.setdefault(dist, []).append(data)

        return by_hop

    def get_stats(self) -> dict:
        """Return graph statistics."""
        type_counts = Counter(
            data.get("type", "unknown")
            for _, data in self.graph.nodes(data=True)
        )
        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "types": dict(type_counts),
        }
