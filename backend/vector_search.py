"""Semantic search over vault nodes using ChromaDB."""

from __future__ import annotations

import logging

import chromadb

logger = logging.getLogger(__name__)

COLLECTION_NAME = "vault_nodes"


class VectorIndex:
    """Embeds and searches vault nodes via ChromaDB."""

    def __init__(self, persist_dir: str) -> None:
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(COLLECTION_NAME)

    def index_all(self, nodes: list[dict]) -> None:
        """Embed all nodes into the collection."""
        if not nodes:
            return

        ids = []
        documents = []
        metadatas = []

        for node in nodes:
            node_id = node["id"]
            title = node.get("title", node_id)
            tags = node.get("tags", [])
            if isinstance(tags, list):
                tags_str = ", ".join(str(t) for t in tags)
            else:
                tags_str = str(tags)
            content = node.get("content", "")

            doc = f"{title}\n{tags_str}\n{content}"

            ids.append(node_id)
            documents.append(doc)
            metadatas.append({
                "type": node.get("type", "unknown"),
                "title": title,
                "tags": tags_str,
            })

        # ChromaDB upsert handles batching internally
        self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        logger.info(f"Indexed {len(ids)} nodes into ChromaDB")

    def search(self, query: str, n: int = 5) -> list[dict]:
        """Semantic search, returning ranked results."""
        try:
            count = self.collection.count()
        except Exception:
            # Collection may be in a bad state — recreate it
            self.collection = self.client.get_or_create_collection(COLLECTION_NAME)
            count = self.collection.count()

        if count == 0:
            return []

        # Don't request more results than exist
        n = min(n, self.collection.count())

        results = self.collection.query(query_texts=[query], n_results=n)

        output = []
        for i, node_id in enumerate(results["ids"][0]):
            output.append({
                "id": node_id,
                "score": results["distances"][0][i] if results["distances"] else 0,
                "title": results["metadatas"][0][i].get("title", node_id),
                "type": results["metadatas"][0][i].get("type", "unknown"),
            })
        return output

    def find_duplicates(self, node_id: str, title: str, node_type: str, n: int = 3) -> list[dict]:
        """Search for existing nodes that might be duplicates of a proposed node.

        Returns matches with similarity scores. Filters to same type for stronger matches.
        """
        try:
            count = self.collection.count()
        except Exception:
            self.collection = self.client.get_or_create_collection(COLLECTION_NAME)
            count = self.collection.count()

        if count == 0:
            return []

        # Check exact ID match first
        try:
            exact = self.collection.get(ids=[node_id])
            if exact and exact["ids"]:
                return [{
                    "id": node_id,
                    "title": exact["metadatas"][0].get("title", node_id),
                    "type": exact["metadatas"][0].get("type", "unknown"),
                    "score": 0.0,
                    "match": "exact_id",
                }]
        except Exception:
            pass

        # Semantic search by title
        n_query = min(n + 2, count)  # fetch a few extra, filter below
        results = self.collection.query(query_texts=[title], n_results=n_query)

        matches = []
        for i, result_id in enumerate(results["ids"][0]):
            if result_id == node_id:
                continue  # skip self if somehow already indexed
            result_type = results["metadatas"][0][i].get("type", "unknown")
            result_title = results["metadatas"][0][i].get("title", result_id)
            distance = results["distances"][0][i] if results["distances"] else 999

            # ChromaDB uses L2 distance — lower = more similar
            # Threshold: < 0.3 is very close, < 0.8 is related
            if distance > 1.0:
                continue

            match_type = "none"
            if result_type == node_type and distance < 0.3:
                match_type = "likely"
            elif result_type == node_type and distance < 0.6:
                match_type = "possible"
            elif distance < 0.3:
                match_type = "possible"

            if match_type != "none":
                matches.append({
                    "id": result_id,
                    "title": result_title,
                    "type": result_type,
                    "score": round(distance, 3),
                    "match": match_type,
                })

        return matches[:n]

    def rebuild(self, nodes: list[dict]) -> None:
        """Delete and recreate the collection, then re-index."""
        try:
            self.client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass  # Collection may already be gone (reloader race)
        self.collection = self.client.get_or_create_collection(COLLECTION_NAME)
        self.index_all(nodes)
