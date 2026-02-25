"""Parses vault markdown files into structured nodes and edges."""

from __future__ import annotations

import os
import re
import logging
import yaml

logger = logging.getLogger(__name__)

# Regex to match [[wikilinks]], capturing just the ID portion
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Default section heading → edge type mapping (used if no schema provided)
DEFAULT_EDGE_MAP = {
    "blockers": "blocked_by",
    "supports": "supported_by",
    "related": "relates_to",
    "contradicts": "contradicts",
    "inspired by": "inspired_by",
    "people": "involves",
    "part of": "part_of",
    "located in": "located_in",
    "funded by": "funded_by",
    "met at": "met_at",
}

SKIP_DIRS = {"_meta", "_templates", "_backup", ".git"}


class VaultParser:
    """Reads vault markdown files, extracts frontmatter, content, and wikilinks."""

    def __init__(self, vault_path: str, edge_map: dict[str, str] | None = None) -> None:
        self.vault_path = os.path.abspath(vault_path)
        if not os.path.isdir(self.vault_path):
            raise FileNotFoundError(f"Vault directory not found: {self.vault_path}")
        self.edge_map = edge_map or DEFAULT_EDGE_MAP

    def parse(self) -> tuple[list[dict], list[tuple[str, str, str]]]:
        """Walk the vault and return (nodes, edges).

        nodes: list of dicts with id, type, title, content, filepath, + frontmatter fields
        edges: list of (source_id, target_id, edge_type) tuples
        """
        nodes = []
        edges = []

        for dirpath, dirnames, filenames in os.walk(self.vault_path):
            # Skip special directories
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

            for filename in filenames:
                if not filename.endswith(".md"):
                    continue

                filepath = os.path.join(dirpath, filename)
                result = self._parse_file(filepath)
                if result is None:
                    continue

                node, file_edges = result
                nodes.append(node)
                edges.extend(file_edges)

        logger.info(f"Parsed {len(nodes)} nodes, {len(edges)} edges from vault")
        return nodes, edges

    def _parse_file(self, filepath: str) -> tuple[dict, list[tuple[str, str, str]]] | None:
        """Parse a single markdown file into a node dict and its edges."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                raw = f.read()
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Could not read {filepath}: {e}")
            return None

        if not raw.strip():
            return None

        frontmatter, body = self._extract_frontmatter(raw)

        node_id = frontmatter.get("id")
        if not node_id:
            logger.warning(f"No 'id' in frontmatter, skipping: {filepath}")
            return None

        # Build relative path from vault root
        rel_path = os.path.relpath(filepath, self.vault_path)

        node = {
            **frontmatter,
            "id": str(node_id),
            "content": body.strip(),
            "filepath": rel_path,
        }

        edges = self._extract_edges(str(node_id), body)
        return node, edges

    def _extract_frontmatter(self, content: str) -> tuple[dict, str]:
        """Split YAML frontmatter from body content.

        Returns (frontmatter_dict, body_string). If no valid frontmatter,
        returns ({}, full_content).
        """
        if not content.startswith("---"):
            return {}, content

        # Find the closing ---
        end = content.find("---", 3)
        if end == -1:
            return {}, content

        yaml_str = content[3:end]
        body = content[end + 3 :].lstrip("\n")

        try:
            fm = yaml.safe_load(yaml_str)
            if not isinstance(fm, dict):
                return {}, content
            return fm, body
        except yaml.YAMLError as e:
            logger.warning(f"Bad YAML frontmatter: {e}")
            return {}, content

    def _extract_edges(
        self, node_id: str, body: str
    ) -> list[tuple[str, str, str]]:
        """Extract edges from wikilinks in the body, using section headings for type."""
        edges = []
        current_section: str | None = None

        for line in body.split("\n"):
            # Check for section heading
            if line.startswith("## "):
                heading = line[3:].strip().lower()
                current_section = heading
                continue

            # Find all wikilinks on this line
            for match in WIKILINK_RE.finditer(line):
                target_id = match.group(1).strip()
                if not target_id:
                    continue

                # Determine edge type from current section
                if current_section and current_section in self.edge_map:
                    edge_type = self.edge_map[current_section]
                else:
                    edge_type = "relates_to"

                edges.append((node_id, target_id, edge_type))

        return edges
