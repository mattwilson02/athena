"""Vault write / update / repair / cross-reference logic."""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

import yaml

from mentor_agent import _get_permanence
from middleware.security import safe_resolve

logger = logging.getLogger(__name__)

# Warning messages by permanence level — added to cascade proposals and direct updates.
_PERMANENCE_WARNINGS: dict[str, str] = {
    "fundamental": (
        "This would modify a human fundamental (movement/sleep/nutrition/connection/purpose/stability). "
        "These are species-level needs — reshapeable but not removable."
    ),
    "identity": (
        "This would modify a core value, belief, or fear (identity-level). "
        "Changes here reshape who you are."
    ),
}


class VaultService:
    """Encapsulates all vault file manipulation."""

    def __init__(self, vault_path: str, graph, vector_index, schema: dict, rebuild_fn, lock: threading.RLock | None = None, refresh_fn=None):
        self.vault_path = vault_path
        self.graph = graph
        self.vector_index = vector_index
        self.schema = schema
        self.rebuild_fn = rebuild_fn
        self.refresh_fn = refresh_fn
        self.lock = lock or threading.RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, data: dict) -> dict:
        """Write a new node to the vault. Returns {ok, filepath, stats, suggested_links} or {error}."""
        with self.lock:
            return self._write_locked(data)

    def _write_locked(self, data: dict) -> dict:
        node_id = data.get("node_id")
        title = data.get("title", node_id)
        node_type = data.get("type")
        content = data.get("content", "")
        frontmatter = data.get("frontmatter", {})

        if not node_id:
            return {"error": "node_id is required", "status": 400}

        node_id = _sanitize_id(node_id)
        if not node_id:
            return {"error": "node_id is empty after sanitization", "status": 400}

        # Validate type against schema
        valid_types = set(self.schema.get("type_list", []))
        if node_type and node_type not in valid_types:
            logger.warning(f"Invalid node type '{node_type}' for node '{node_id}' — rejecting")
            return {
                "error": f"Invalid type '{node_type}'. Valid types: {', '.join(sorted(valid_types))}",
                "status": 400,
            }

        # Resolve folder from schema
        from schema_parser import get_folder_for_type

        folder = get_folder_for_type(self.schema, node_type) or data.get("folder")
        if not folder:
            return {"error": f"Unknown type '{node_type}' and no folder provided", "status": 400}

        try:
            safe_resolve(Path(self.vault_path), folder)
        except ValueError:
            return {"error": "Invalid folder path", "status": 400}

        # Ensure frontmatter has core fields
        frontmatter["id"] = node_id
        frontmatter.setdefault("type", node_type)
        frontmatter.setdefault("title", title)

        fm_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
        body = f"# {title}\n\n{content}\n"

        # Write edges as wikilinks under section headings
        edges = data.get("edges", [])
        if edges:
            sections: dict[str, list[str]] = {}
            for edge in edges:
                target = edge.get("target", "")
                edge_type = edge.get("type", "relates_to")
                if target:
                    target = _sanitize_id(target)
                    section = _edge_type_to_section(edge_type)
                    sections.setdefault(section, []).append(target)

            for section_name, targets in sections.items():
                body += f"\n## {section_name}\n"
                for t in targets:
                    body += f"- [[{t}]]\n"

        md = f"---\n{fm_str}---\n\n{body}"

        filepath = os.path.join(self.vault_path, folder, f"{node_id}.md")
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)

        if self.refresh_fn:
            stats = self.refresh_fn(node_id)
        else:
            stats = self.rebuild_fn()
        suggested_links = self.find_cross_references(node_id)

        return {
            "ok": True,
            "filepath": f"{folder}/{node_id}.md",
            "stats": stats,
            "suggested_links": suggested_links,
        }

    def update(self, data: dict) -> dict:
        """Update an existing node. Returns {ok, node_id, stats} or {error}."""
        with self.lock:
            return self._update_locked(data)

    def _update_locked(self, data: dict) -> dict:
        node_id = data.get("node_id")
        if not node_id:
            return {"error": "node_id is required", "status": 400}

        node_id = _sanitize_id(node_id)

        node = self.graph.get_node(node_id)
        if node is None:
            return {"error": f"Node '{node_id}' not found", "status": 404}

        filepath = os.path.join(self.vault_path, node["filepath"])
        if not os.path.isfile(filepath):
            return {"error": f"File not found for node '{node_id}'", "status": 404}

        with open(filepath, "r", encoding="utf-8") as f:
            raw = f.read()

        fm, body = _split_frontmatter(raw)
        body = body.lstrip("\n")
        changes = data.get("changes", {})

        # Replace title
        new_title = changes.get("title")
        if new_title:
            fm["title"] = new_title
            body = re.sub(r"^# .+", f"# {new_title}", body, count=1)

        # Replace content
        new_content = changes.get("content")
        if new_content:
            section_match = re.search(r"\n## ", body)
            if section_match:
                heading_match = re.match(r"# .+\n", body)
                heading = heading_match.group(0) if heading_match else f"# {fm.get('title', node_id)}\n"
                body = heading + "\n" + new_content + "\n" + body[section_match.start():]
            else:
                heading_match = re.match(r"# .+\n", body)
                heading = heading_match.group(0) if heading_match else f"# {fm.get('title', node_id)}\n"
                body = heading + "\n" + new_content + "\n"

        # Patch frontmatter
        fm_updates = changes.get("frontmatter", {})
        if fm_updates:
            fm.update(fm_updates)

        from datetime import date

        fm["updated"] = date.today().isoformat()

        # Add tags
        add_tags = changes.get("add_tags", [])
        if add_tags:
            existing_tags = fm.get("tags", [])
            if not isinstance(existing_tags, list):
                existing_tags = [existing_tags] if existing_tags else []
            for tag in add_tags:
                if tag not in existing_tags:
                    existing_tags.append(tag)
            fm["tags"] = existing_tags

        # Remove tags
        remove_tags = changes.get("remove_tags", [])
        if remove_tags and isinstance(fm.get("tags"), list):
            fm["tags"] = [t for t in fm["tags"] if t not in remove_tags]

        # Append content
        append_content = changes.get("append_content", "")
        if append_content:
            section_match = re.search(r"\n## ", body)
            if section_match:
                pos = section_match.start()
                body = body[:pos] + "\n\n" + append_content + body[pos:]
            else:
                body = body.rstrip() + "\n\n" + append_content + "\n"

        # Add edges
        for edge in changes.get("add_edges", []):
            target = edge.get("target", "")
            edge_type = edge.get("type", "relates_to")
            if target:
                target = _sanitize_id(target)
                section = _edge_type_to_section(edge_type)
                body = _add_wikilink_to_section(body, section, target)

        body = _dedup_sections(body)

        fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"---\n{fm_str}---\n\n{body}")

        if self.refresh_fn:
            stats = self.refresh_fn(node_id)
        else:
            stats = self.rebuild_fn()
        return {"ok": True, "node_id": node_id, "stats": stats}

    def repair(self) -> dict:
        """Fix duplicate sections and heading issues across all vault files."""
        with self.lock:
            return self._repair_locked()

    def _repair_locked(self) -> dict:
        skip_dirs = {"_meta", "_templates", "_backup", ".git"}
        count = 0

        for dirpath, dirnames, filenames in os.walk(self.vault_path):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                filepath = os.path.join(dirpath, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    raw = f.read()

                fm, body = _split_frontmatter(raw)
                if not fm:
                    continue

                body = body.lstrip("\n")
                original_body = body

                heading_match = re.match(r"(# .+\n)\s*\n?(# .+\n)", body)
                if heading_match:
                    body = body[heading_match.end(1):].lstrip("\n")

                body = _dedup_sections(body)

                if body != original_body:
                    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(f"---\n{fm_str}---\n\n{body}")
                    count += 1
                    logger.info(f"Repaired: {filepath}")

        stats = self.rebuild_fn()
        return {"ok": True, "repaired_files": count, "stats": stats}

    def cascade_check(self, node_id: str, changes: dict) -> list[dict]:
        """Find nodes affected by a change. Returns cascade proposals.

        Uses two strategies:
        1. Graph cascade — 1-hop neighbors, check for stale references
        2. Semantic cascade — vector search for unlinked-but-related nodes
        """
        node = self.graph.get_node(node_id)
        if node is None:
            return []

        proposals: list[dict] = []
        seen_ids: set[str] = set()

        # What changed?
        fm_changes = changes.get("frontmatter", {})
        new_status = fm_changes.get("status", "").lower() if fm_changes.get("status") else ""
        status_terminal = new_status in ("cancelled", "completed", "done", "abandoned", "superseded")
        date_changed = any(fm_changes.get(f) for f in ("date", "due", "deadline"))

        # Only cascade when there's a meaningful change (status or date).
        # Prevents infinite cascade loops from link-only updates.
        if not status_terminal and not date_changed:
            return []

        # --- Step 1: Graph cascade (1-hop neighbors) ---
        neighbors = self.graph.get_neighbors(node_id, depth=1)
        for neighbor in neighbors:
            nid = neighbor["id"]
            if nid == node_id:
                continue
            n_status = str(neighbor.get("status", "") or "").lower()
            n_type = neighbor.get("type", "")

            if status_terminal and n_status in ("active", "pending", "in-progress", ""):
                if n_type in ("task", "reminder", "event"):
                    proposals.append({
                        "action": "update",
                        "node_id": nid,
                        "title": neighbor.get("title", nid),
                        "type": n_type,
                        "changes": {"frontmatter": {"status": new_status}},
                        "reason": f"Linked to '{node.get('title', node_id)}' which was just {new_status}",
                        "confidence": "graph",
                    })
                    seen_ids.add(nid)
                else:
                    proposals.append({
                        "action": "update",
                        "node_id": nid,
                        "title": neighbor.get("title", nid),
                        "type": n_type,
                        "changes": {},
                        "reason": f"Linked to '{node.get('title', node_id)}' which was just {new_status} — may need review",
                        "confidence": "graph",
                    })
                    seen_ids.add(nid)

            elif date_changed and n_type in ("task", "reminder", "event"):
                proposals.append({
                    "action": "update",
                    "node_id": nid,
                    "title": neighbor.get("title", nid),
                    "type": n_type,
                    "changes": {},
                    "reason": f"Linked to '{node.get('title', node_id)}' whose date just changed — check if still correct",
                    "confidence": "graph",
                })
                seen_ids.add(nid)

        # --- Step 2: Semantic cascade (unlinked but related) ---
        neighbor_ids = {n["id"] for n in neighbors}
        title = node.get("title", node_id)
        change_summary = f"{title} {new_status}" if new_status else title

        try:
            semantic_results = self.vector_index.search(change_summary, n=8)
        except Exception as e:
            logger.warning(f"Semantic cascade search failed: {e}")
            semantic_results = []

        for result in semantic_results:
            rid = result["id"]
            if rid == node_id or rid in seen_ids or rid in neighbor_ids:
                continue

            r_node = self.graph.get_node(rid)
            if r_node is None:
                continue

            r_status = str(r_node.get("status", "") or "").lower()
            r_type = r_node.get("type", "")

            # Apply staleness checks same as graph cascade
            if status_terminal and r_status in ("active", "pending", "in-progress", ""):
                if r_type in ("task", "reminder", "event"):
                    proposals.append({
                        "action": "update",
                        "node_id": rid,
                        "title": r_node.get("title", rid),
                        "type": r_type,
                        "changes": {
                            "frontmatter": {"status": new_status},
                            "add_edges": [{"target": node_id, "type": _infer_edge_type(r_type, node.get("type", ""))}],
                        },
                        "reason": f"Not linked but related to '{title}' which was just {new_status} — still marked {r_status or 'active'}",
                        "confidence": "semantic",
                    })
                    seen_ids.add(rid)
                    continue

            # No staleness — propose link only
            proposals.append({
                "action": "link",
                "source": rid,
                "target": node_id,
                "edge_type": _infer_edge_type(r_type, node.get("type", "")),
                "title": r_node.get("title", rid),
                "type": r_type,
                "reason": f"Semantically related to '{title}' but not linked",
                "confidence": "semantic",
            })
            seen_ids.add(rid)

        # --- Step 3: Limit and rank (graph first, then semantic) ---
        graph_proposals = [p for p in proposals if p.get("confidence") == "graph"][:3]
        semantic_proposals = [p for p in proposals if p.get("confidence") == "semantic"][:2]
        result_proposals = graph_proposals + semantic_proposals

        # --- Step 4: Annotate proposals targeting identity/fundamental nodes ---
        for proposal in result_proposals:
            p_type = proposal.get("type", "")
            level, _ = _get_permanence(p_type)
            warning = _PERMANENCE_WARNINGS.get(level)
            if warning:
                proposal["permanence_warning"] = warning

        return result_proposals

    def find_cross_references(self, node_id: str, max_suggestions: int = 5) -> list[dict]:
        """Find potential links for a node by scanning existing nodes."""
        node = self.graph.get_node(node_id)
        if node is None:
            return []

        title = node.get("title", "")
        node_type = node.get("type", "")
        content = node.get("content", "")
        existing_neighbors = {n["id"] for n in self.graph.get_neighbors(node_id, depth=1)}
        suggestions: list[dict] = []
        seen_targets: set[str] = set()

        all_nodes = self.graph.get_all_nodes()

        # 1. Reverse scan: existing nodes that mention this node
        if title:
            title_lower = title.lower()
            for other in all_nodes:
                if other["id"] == node_id or other["id"] in existing_neighbors:
                    continue
                other_content = other.get("content", "")
                other_title = other.get("title", "")
                if title_lower in other_content.lower() or title_lower in other_title.lower():
                    if other["id"] not in seen_targets:
                        suggestions.append({
                            "source": other["id"],
                            "target": node_id,
                            "type": _infer_edge_type(other.get("type", ""), node_type),
                            "reason": f'"{other.get("title", other["id"])}" mentions "{title}"',
                        })
                        seen_targets.add(other["id"])

        # 2. Forward scan: this node mentions existing node titles
        if content:
            content_lower = content.lower()
            for other in all_nodes:
                if other["id"] == node_id or other["id"] in existing_neighbors:
                    continue
                if other["id"] in seen_targets:
                    continue
                other_title = other.get("title", "")
                if other_title and len(other_title) > 2 and other_title.lower() in content_lower:
                    suggestions.append({
                        "source": node_id,
                        "target": other["id"],
                        "type": _infer_edge_type(node_type, other.get("type", "")),
                        "reason": f'Content mentions "{other_title}"',
                    })
                    seen_targets.add(other["id"])

        # 3. Semantic similarity
        try:
            search_text = f"{title} {content[:200]}" if content else title
            similar = self.vector_index.search(search_text, n=8)
            for result in similar:
                rid = result["id"]
                if rid == node_id or rid in existing_neighbors or rid in seen_targets:
                    continue
                if result.get("score", 999) > 0.8:
                    continue
                suggestions.append({
                    "source": node_id,
                    "target": rid,
                    "type": _infer_edge_type(node_type, result.get("type", "")),
                    "reason": f'Semantically related to "{result.get("title", rid)}"',
                })
                seen_targets.add(rid)
        except Exception as e:
            logger.warning(f"Semantic search failed during cross-ref: {e}")

        return suggestions[:max_suggestions]

    def import_proposals(self, source_dir: str = "_backup") -> dict:
        """Scan archived nodes, validate types, dedup-check, return proposals."""
        backup_path = Path(self.vault_path) / source_dir
        if not backup_path.is_dir():
            return {"proposals": [], "summary": {"total": 0, "ready": 0, "duplicate": 0, "needs_fix": 0}}

        valid_types = set(self.schema.get("type_list", []))
        proposals = []

        for md_file in sorted(backup_path.rglob("*.md")):
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    raw = f.read()
            except OSError:
                continue

            fm, body = _split_frontmatter(raw)
            node_id = fm.get("id", md_file.stem)
            node_type = fm.get("type", "note")
            title = fm.get("title", node_id)

            # Strip the markdown heading from body if present
            content = body.strip()
            if content.startswith(f"# {title}"):
                content = content[len(f"# {title}"):].strip()

            # Type validation
            type_valid = node_type in valid_types
            suggested_type = _suggest_type(node_type, valid_types) if not type_valid else None

            # Dedup check
            duplicate = None
            existing = self.graph.get_node(node_id)
            if existing:
                duplicate = {
                    "match": "exact_id",
                    "existing_id": existing["id"],
                    "existing_title": existing.get("title", existing["id"]),
                }
            else:
                try:
                    matches = self.vector_index.find_duplicates(node_id, title, node_type)
                    if matches:
                        duplicate = {
                            "match": matches[0]["match"],
                            "existing_id": matches[0]["id"],
                            "existing_title": matches[0]["title"],
                            "score": matches[0].get("score", 0),
                        }
                except Exception:
                    pass

            # Determine status
            if duplicate:
                status = "duplicate"
            elif not type_valid:
                status = "needs_fix"
            else:
                status = "ready"

            proposals.append({
                "source_file": str(md_file.relative_to(Path(self.vault_path))),
                "node_id": node_id,
                "title": title,
                "type": node_type,
                "type_valid": type_valid,
                "suggested_type": suggested_type,
                "content": content,
                "frontmatter": fm,
                "duplicate": duplicate,
                "status": status,
            })

        summary = {
            "total": len(proposals),
            "ready": sum(1 for p in proposals if p["status"] == "ready"),
            "duplicate": sum(1 for p in proposals if p["status"] == "duplicate"),
            "needs_fix": sum(1 for p in proposals if p["status"] == "needs_fix"),
        }

        return {"proposals": proposals, "summary": summary}

    def import_accept(self, node_id: str, source_dir: str = "_backup",
                      type_override: str | None = None) -> dict:
        """Accept an import proposal — write the archived node to the vault."""
        backup_path = Path(self.vault_path) / source_dir
        if not backup_path.is_dir():
            return {"error": f"Source directory '{source_dir}' not found", "status": 404}

        # Find the source file
        source_file = None
        for md_file in backup_path.rglob("*.md"):
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    raw = f.read()
            except OSError:
                continue
            fm, _ = _split_frontmatter(raw)
            if fm.get("id", md_file.stem) == node_id:
                source_file = md_file
                break

        if not source_file:
            return {"error": f"Node '{node_id}' not found in {source_dir}", "status": 404}

        with open(source_file, "r", encoding="utf-8") as f:
            raw = f.read()
        fm, body = _split_frontmatter(raw)
        title = fm.get("title", node_id)
        node_type = type_override or fm.get("type", "note")

        # Strip the markdown heading from body
        content = body.strip()
        if content.startswith(f"# {title}"):
            content = content[len(f"# {title}"):].strip()

        # Clean frontmatter for fresh import
        clean_fm = {}
        for key in ("status", "priority", "context"):
            if key in fm:
                clean_fm[key] = fm[key]
        # Preserve tags
        if fm.get("tags"):
            pass  # tags handled separately by write()

        return self.write({
            "node_id": node_id,
            "title": title,
            "type": node_type,
            "content": content,
            "frontmatter": clean_fm,
            "edges": [],
        })


# ------------------------------------------------------------------
# Module-level helpers (pure functions, no class state needed)
# ------------------------------------------------------------------


def _sanitize_id(raw: str) -> str:
    """Normalize to lowercase alphanumeric with hyphens."""
    node_id = raw.lower().replace("_", "-").replace(" ", "-")
    node_id = re.sub(r"[^a-z0-9-]", "", node_id)
    node_id = re.sub(r"-+", "-", node_id).strip("-")
    return node_id


_TYPE_REMAPS = {
    "lesson": "pill",
    "film": "movie",
    "show": "movie",
    "contact": "person",
    "journal": "daily",
    "todo": "task",
    "interest": "skill",
    "aspiration": "goal",
}


def _suggest_type(invalid_type: str, valid_types: set) -> str | None:
    """Suggest a valid type for an invalid one using known remaps."""
    remapped = _TYPE_REMAPS.get(invalid_type.lower())
    if remapped and remapped in valid_types:
        return remapped
    return "note"


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    try:
        fm = yaml.safe_load(content[3:end])
        if not isinstance(fm, dict):
            return {}, content
        return fm, content[end + 3:].lstrip("\n")
    except yaml.YAMLError:
        return {}, content


def _edge_type_to_section(edge_type: str) -> str:
    """Map edge type back to a section heading name."""
    reverse = {
        "blocked_by": "Blockers", "supported_by": "Supports", "relates_to": "Related",
        "contradicts": "Contradicts", "inspired_by": "Inspired By", "involves": "People",
        "part_of": "Part Of", "located_in": "Located In", "funded_by": "Funded By",
        "met_at": "Met At",
    }
    return reverse.get(edge_type, "Related")


def _dedup_sections(body: str) -> str:
    """Merge duplicate ## sections, combining wikilinks into the first occurrence."""
    lines = body.split("\n")
    sections: list[tuple[str | None, list[str]]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            sections.append((current_heading, current_lines))
            current_heading = line
            current_lines = []
        else:
            current_lines.append(line)
    sections.append((current_heading, current_lines))

    seen: dict[str, int] = {}
    merged: list[tuple[str | None, list[str]]] = []

    for heading, content in sections:
        if heading is None:
            merged.append((heading, content))
            continue

        if heading in seen:
            first_idx = seen[heading]
            first_content = merged[first_idx][1]
            existing_links = {m.group(1) for m in re.finditer(r"\[\[([^\]]+)\]\]",
                              "\n".join(first_content))}
            for cl in content:
                link_match = re.search(r"\[\[([^\]]+)\]\]", cl)
                if link_match and link_match.group(1) not in existing_links:
                    first_content.append(cl)
                    existing_links.add(link_match.group(1))
        else:
            seen[heading] = len(merged)
            merged.append((heading, content))

    result_lines: list[str] = []
    for heading, content in merged:
        if heading is not None:
            result_lines.append(heading)
        result_lines.extend(content)

    return "\n".join(result_lines)


def _add_wikilink_to_section(body: str, section_name: str, target_id: str) -> str:
    """Add a [[wikilink]] under a section heading. Creates section if missing."""
    if f"[[{target_id}]]" in body:
        return body

    wikilink = f"- [[{target_id}]]"
    pattern = re.compile(rf"^## {re.escape(section_name)}\s*$", re.MULTILINE)
    match = pattern.search(body)

    if match:
        insert_pos = match.end()
        rest = body[insert_pos:]
        lines = rest.split("\n")
        skip = 0
        for line in lines:
            s = line.strip()
            if s == "" or s.startswith("<!--") or s.endswith("-->"):
                skip += 1
            else:
                break
        insert_pos += sum(len(lines[i]) + 1 for i in range(skip))
        body = body[:insert_pos] + wikilink + "\n" + body[insert_pos:]
    else:
        body = body.rstrip() + f"\n\n## {section_name}\n{wikilink}\n"

    return body


def _infer_edge_type(source_type: str, target_type: str) -> str:
    """Infer a sensible default edge type from source and target node types."""
    if target_type == "person" or source_type == "person":
        return "involves"
    if target_type == "place" or source_type == "place":
        return "located_in"
    if target_type == "project" or source_type == "project":
        return "part_of"
    if target_type == "budget" or source_type == "budget":
        return "funded_by"
    if target_type == "book" or target_type == "article":
        return "inspired_by"
    return "relates_to"
