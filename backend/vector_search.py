"""Semantic search over vault nodes using ChromaDB."""

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

    def rebuild(self, nodes: list[dict]) -> None:
        """Delete and recreate the collection, then re-index."""
        self.client.delete_collection(COLLECTION_NAME)
        self.collection = self.client.create_collection(COLLECTION_NAME)
        self.index_all(nodes)
