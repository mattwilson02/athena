"""Parses vault/_meta/schema.md into a structured schema dict.

The schema file is the single source of truth for domains, types,
frontmatter fields, folder mappings, and edge types. This module reads
it at boot so nothing is hardcoded in Python.
"""

from __future__ import annotations

import re
import logging

import yaml

logger = logging.getLogger(__name__)

# Marker comments in schema.md that delimit parseable sections
_DOMAINS_RE = re.compile(
    r"<!-- PARSER:DOMAINS_START -->\s*\n(.*?)\n\s*<!-- PARSER:DOMAINS_END -->",
    re.DOTALL,
)
_TYPES_RE = re.compile(
    r"<!-- PARSER:TYPES_START -->\s*\n(.*?)\n\s*<!-- PARSER:TYPES_END -->",
    re.DOTALL,
)
_EDGES_RE = re.compile(
    r"<!-- PARSER:EDGES_START -->\s*\n(.*?)\n\s*<!-- PARSER:EDGES_END -->",
    re.DOTALL,
)


def parse_schema(schema_path: str) -> dict:
    """Parse schema.md and return a structured schema dict.

    Returns:
        {
            "domains": {
                "Self": {"folder": "Self", "description": "...", "types": ["goal", ...]},
                ...
            },
            "types": {
                "goal": {
                    "domain": "Self",
                    "folder": "Self/Goals",
                    "description": "...",
                    "frontmatter": {"status": "active", "priority": "medium", ...},
                },
                ...
            },
            "edges": {
                "blockers": "blocked_by",
                "supports": "supported_by",
                ...
            },
            "type_list": ["goal", "fear", ...],
            "domain_list": ["Self", "People", ...],
        }
    """
    with open(schema_path, "r", encoding="utf-8") as f:
        content = f.read()

    domains = _parse_domains(content)
    types = _parse_types(content)
    edges = _parse_edges(content)

    # Cross-reference: add type lists to domains
    for type_name, type_info in types.items():
        domain_name = type_info.get("domain")
        if domain_name and domain_name in domains:
            domains[domain_name].setdefault("types", []).append(type_name)

    schema = {
        "domains": domains,
        "types": types,
        "edges": edges,
        "type_list": sorted(types.keys()),
        "domain_list": sorted(domains.keys()),
    }

    logger.info(
        f"Schema parsed: {len(domains)} domains, {len(types)} types, {len(edges)} edge types"
    )
    return schema


def _parse_domains(content: str) -> dict:
    """Extract domain definitions from the markdown table."""
    match = _DOMAINS_RE.search(content)
    if not match:
        logger.warning("No DOMAINS section found in schema")
        return {}

    domains = {}
    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("|--") or line.startswith("| Domain"):
            continue
        parts = [p.strip() for p in line.split("|")]
        # Table row: | Domain | Folder | Description |
        parts = [p for p in parts if p]
        if len(parts) >= 3:
            name, folder, description = parts[0], parts[1], parts[2]
            domains[name] = {"folder": folder, "description": description}

    return domains


def _parse_types(content: str) -> dict:
    """Extract type definitions from the ### type_name sections."""
    match = _TYPES_RE.search(content)
    if not match:
        logger.warning("No TYPES section found in schema")
        return {}

    types = {}
    section_text = match.group(1)

    # Split on ### headings — use regex findall to get each block cleanly
    type_blocks = re.findall(r"### (\S+)\n(.*?)(?=\n### |\Z)", section_text, re.DOTALL)

    for type_name, block_body in type_blocks:
        type_name = type_name.strip()
        block = block_body.strip()
        if not type_name:
            continue

        type_info = {"frontmatter": {}}
        lines = block.split("\n")

        for line in lines:
            line = line.strip()
            if line.startswith("- **Domain:**"):
                type_info["domain"] = line.split("**Domain:**")[1].strip()
            elif line.startswith("- **Folder:**"):
                type_info["folder"] = line.split("**Folder:**")[1].strip()
            elif line.startswith("- **Description:**"):
                type_info["description"] = line.split("**Description:**")[1].strip()

        # Extract frontmatter from yaml code block
        yaml_match = re.search(r"```yaml\n(.*?)```", block, re.DOTALL)
        if yaml_match:
            yaml_str = yaml_match.group(1).strip()
            # Strip comments for parsing but keep them for reference
            clean_lines = []
            for yl in yaml_str.split("\n"):
                # Remove inline comments
                stripped = re.sub(r"\s*#.*$", "", yl)
                if stripped.strip():
                    clean_lines.append(stripped)
            if clean_lines:
                try:
                    fm = yaml.safe_load("\n".join(clean_lines))
                    if isinstance(fm, dict):
                        type_info["frontmatter"] = fm
                except yaml.YAMLError:
                    pass

        types[type_name] = type_info

    return types


def _parse_edges(content: str) -> dict:
    """Extract section heading → edge type mapping from the edges table."""
    match = _EDGES_RE.search(content)
    if not match:
        logger.warning("No EDGES section found in schema")
        return {}

    edges = {}
    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("|--") or line.startswith("| Section"):
            continue

        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            heading_raw = parts[0]
            edge_type = parts[1].strip().strip("`")

            # Extract heading text from backtick-wrapped markdown
            # e.g. `## Blockers` → "blockers"
            heading_match = re.search(r"##\s*(.+?)(?:`|$)", heading_raw)
            if heading_match:
                heading = heading_match.group(1).strip().lower()
                edges[heading] = edge_type
            elif "outside any section" in heading_raw.lower():
                edges["_default"] = edge_type

    return edges


def generate_type_rules(schema: dict) -> str:
    """Generate the NODE TYPE RULES text for the system prompt from the parsed schema.

    Groups types by domain with descriptions, producing a concise reference
    the AI can use for triage.
    """
    lines = []
    lines.append("NODE TYPE RULES — pick the right domain, then the right type:\n")

    for domain_name in schema["domain_list"]:
        domain = schema["domains"][domain_name]
        domain_types = domain.get("types", [])
        if not domain_types:
            continue

        lines.append(f"**{domain_name}** — {domain['description']}")
        for type_name in domain_types:
            type_info = schema["types"][type_name]
            desc = type_info.get("description", "")
            lines.append(f"  - {type_name}: {desc}")
        lines.append("")

    return "\n".join(lines)


def get_folder_for_type(schema: dict, type_name: str) -> str | None:
    """Return the vault folder path for a given type, or None if unknown."""
    type_info = schema["types"].get(type_name)
    if type_info:
        return type_info.get("folder")
    return None


def get_edge_map(schema: dict) -> dict[str, str]:
    """Return the section heading → edge type mapping for the vault parser."""
    return {k: v for k, v in schema["edges"].items() if k != "_default"}
