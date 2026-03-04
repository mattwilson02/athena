"""Vault audit — scans every node for known issues, returns a structured report."""

from __future__ import annotations

import os
import re
import logging
from datetime import date

from schema_parser import get_folder_for_type

logger = logging.getLogger(__name__)

# Same regex used by VaultParser
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Directories the parser skips — we skip them too
SKIP_DIRS = {"_meta", "_templates", "_backup", ".git"}


def audit_vault(graph, schema: dict, vault_path: str) -> dict:
    """Scan the vault for stale statuses, orphans, broken wikilinks, and type mismatches.

    Pure function — no side effects, no writes.
    """
    all_nodes = graph.get_all_nodes()
    all_ids = {n["id"] for n in all_nodes}
    today = date.today()

    stale_status = _check_stale_statuses(all_nodes, today)
    orphans = _check_orphans(all_nodes, graph)
    broken_wikilinks = _check_broken_wikilinks(vault_path, all_ids)
    type_mismatches = _check_type_mismatches(all_nodes, schema)

    return {
        "total_nodes": len(all_nodes),
        "issues": {
            "stale_status": stale_status,
            "orphans": orphans,
            "broken_wikilinks": broken_wikilinks,
            "type_mismatches": type_mismatches,
        },
        "summary": {
            "stale_status": len(stale_status),
            "orphans": len(orphans),
            "broken_wikilinks": len(broken_wikilinks),
            "type_mismatches": len(type_mismatches),
        },
    }


def _check_stale_statuses(nodes: list[dict], today: date) -> list[dict]:
    """Find nodes with active/planned status and a date field in the past."""
    stale = []
    date_fields = ("date", "due", "deadline")

    for node in nodes:
        status = node.get("status")
        if not status:
            continue

        status_lower = str(status).lower()
        if status_lower not in ("active", "planned"):
            continue

        for field in date_fields:
            val = node.get(field)
            if val is None:
                continue

            parsed = _parse_date(val)
            if parsed is None:
                continue

            if parsed < today:
                stale.append({
                    "id": node["id"],
                    "status": status,
                    "date_field": field,
                    "date_value": str(val),
                })
                break  # one issue per node is enough

    return stale


def _check_orphans(nodes: list[dict], graph) -> list[dict]:
    """Find nodes with zero edges (in + out)."""
    orphans = []
    for node in nodes:
        if graph.get_degree(node["id"]) == 0:
            orphans.append({
                "id": node["id"],
                "type": node.get("type", ""),
                "title": node.get("title", node["id"]),
            })
    return orphans


def _check_broken_wikilinks(vault_path: str, all_ids: set[str]) -> list[dict]:
    """Re-read raw markdown files and find wikilinks pointing to nonexistent nodes.

    Can't use graph edges because build_from_parsed() silently drops dangling edges.
    """
    broken = []

    for dirpath, dirnames, filenames in os.walk(vault_path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in filenames:
            if not filename.endswith(".md"):
                continue

            filepath = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(filepath, vault_path)

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    raw = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            # Extract source node ID from frontmatter
            source_id = _extract_id_from_frontmatter(raw)
            if not source_id:
                continue

            # Find all wikilink targets in the file
            for match in WIKILINK_RE.finditer(raw):
                target = match.group(1).strip()
                if target and target not in all_ids:
                    broken.append({
                        "source": source_id,
                        "target": target,
                        "file": rel_path,
                    })

    return broken


def _check_type_mismatches(nodes: list[dict], schema: dict) -> list[dict]:
    """Find nodes whose filepath doesn't match the expected folder for their type."""
    mismatches = []

    for node in nodes:
        node_type = node.get("type")
        if not node_type:
            continue

        expected_folder = get_folder_for_type(schema, node_type)
        if expected_folder is None:
            # Type not in schema — flag it
            mismatches.append({
                "id": node["id"],
                "type": node_type,
                "filepath": node.get("filepath", ""),
                "expected_folder": None,
            })
            continue

        filepath = node.get("filepath", "")
        if not filepath.startswith(expected_folder):
            mismatches.append({
                "id": node["id"],
                "type": node_type,
                "filepath": filepath,
                "expected_folder": expected_folder,
            })

    return mismatches


def _parse_date(val) -> date | None:
    """Try to parse a date from various formats."""
    if isinstance(val, date):
        return val
    if not isinstance(val, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            from datetime import datetime
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _extract_id_from_frontmatter(raw: str) -> str | None:
    """Quick extraction of the id field from YAML frontmatter."""
    if not raw.startswith("---"):
        return None
    end = raw.find("---", 3)
    if end == -1:
        return None
    # Simple regex instead of full YAML parse for speed
    match = re.search(r"^id:\s*(.+)$", raw[3:end], re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None
