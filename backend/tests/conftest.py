"""Shared test fixtures for Athena backend tests."""

from __future__ import annotations

import os
import sys

import pytest

# Ensure backend/ is on the import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vault_parser import VaultParser
from vault_graph import VaultGraph
from schema_parser import parse_schema
from chat_store import ChatStore


# ---------------------------------------------------------------------------
# Minimal schema.md for test fixtures
# ---------------------------------------------------------------------------

MINI_SCHEMA = """\
---
id: schema
type: _meta
title: Test Schema
---

# Test Schema

## Domains

<!-- PARSER:DOMAINS_START -->
| Domain | Folder | Description |
|--------|--------|-------------|
| Self | Self | Inner world |
| People | People | Your network |
| Knowledge | Knowledge | Things learned |
<!-- PARSER:DOMAINS_END -->

## Node Types

<!-- PARSER:TYPES_START -->

### goal
- **Domain:** Self
- **Folder:** Self/Goals
- **Description:** Something you want to achieve.
- **Frontmatter:**
```yaml
status: active
priority: medium
```

### fear
- **Domain:** Self
- **Folder:** Self/Fears
- **Description:** Something causing anxiety.
- **Frontmatter:**
```yaml
intensity: 5
status: active
```

### person
- **Domain:** People
- **Folder:** People/Persons
- **Description:** An individual in your life.
- **Frontmatter:**
```yaml
relationship: friend
```

### note
- **Domain:** Knowledge
- **Folder:** Knowledge/Notes
- **Description:** A piece of knowledge.
- **Frontmatter:**
```yaml
source:
```

<!-- PARSER:TYPES_END -->

## Edge Types

<!-- PARSER:EDGES_START -->
| Section Heading | Edge Type |
|-----------------|-----------|
| `## Blockers` | `blocked_by` |
| `## Supports` | `supported_by` |
| `## Related` | `relates_to` |
| `## People` | `involves` |
| `## Part Of` | `part_of` |
| Links outside any section | `relates_to` |
<!-- PARSER:EDGES_END -->
"""


# ---------------------------------------------------------------------------
# Test vault nodes as raw markdown
# ---------------------------------------------------------------------------

NODES = {
    "Self/Goals/learn-piano.md": """\
---
id: learn-piano
type: goal
title: Learn Piano
created: 2026-01-15
updated: 2026-02-20
tags:
  - music
  - learning
status: active
priority: high
---

# Learn Piano

I want to learn to play piano at an intermediate level.

## Blockers
- [[time-management]]

## Related
- [[music-theory]]
""",
    "Self/Goals/time-management.md": """\
---
id: time-management
type: goal
title: Better Time Management
created: 2026-01-10
updated: 2026-01-20
tags:
  - productivity
status: active
priority: medium
---

# Better Time Management

Getting better at managing time across projects.
""",
    "Knowledge/Notes/music-theory.md": """\
---
id: music-theory
type: note
title: Music Theory Basics
created: 2026-02-01
tags:
  - music
---

# Music Theory Basics

Scales, chords, and progressions.

## People
- [[alice]]
""",
    "People/Persons/alice.md": """\
---
id: alice
type: person
title: Alice
created: 2026-01-01
tags:
  - friend
relationship: friend
---

# Alice

Musician friend who's helping me learn piano.
""",
}


@pytest.fixture
def tmp_vault(tmp_path):
    """Create a minimal vault directory with test nodes and a schema."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # Write schema
    meta = vault / "_meta"
    meta.mkdir()
    (meta / "schema.md").write_text(MINI_SCHEMA)

    # Write node files
    for rel_path, content in NODES.items():
        filepath = vault / rel_path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)

    # Create skip dirs (should be ignored by parser)
    (vault / "_templates").mkdir()
    (vault / "_templates" / "goal.md").write_text("---\nid: template\ntype: goal\n---\n")
    (vault / "_backup").mkdir()
    (vault / "_backup" / "old.md").write_text("---\nid: old\ntype: goal\n---\n")

    return vault


@pytest.fixture
def schema(tmp_vault):
    """Parse the test schema and return the schema dict."""
    return parse_schema(str(tmp_vault / "_meta" / "schema.md"))


@pytest.fixture
def parser(tmp_vault, schema):
    """VaultParser pointed at the tmp vault."""
    from schema_parser import get_edge_map
    return VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))


@pytest.fixture
def parsed(parser):
    """Parsed nodes and edges from the tmp vault."""
    return parser.parse()


@pytest.fixture
def graph(parsed):
    """VaultGraph built from the parsed tmp vault."""
    nodes, edges = parsed
    g = VaultGraph()
    g.build_from_parsed(nodes, edges)
    return g


@pytest.fixture
def chat_store(tmp_path):
    """ChatStore using a temp directory."""
    return ChatStore(str(tmp_path / "chat_sessions"))
