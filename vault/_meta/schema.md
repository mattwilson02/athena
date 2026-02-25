---
id: schema
type: _meta
title: Athena Vault Schema
created: 2026-02-23
updated: 2026-02-23
---

# Athena Vault Schema

This document defines the ontology, frontmatter schema, and relationship conventions for the Athena knowledge graph.

---

## Node Types

| Type | Folder | Description |
|------|--------|-------------|
| `person` | People/ | Relationships, mentors, friends, family |
| `goal` | Goals/ | Things you're working toward |
| `fear` | Fears/ | Things holding you back |
| `belief` | Beliefs/ | Core convictions, mental models |
| `value` | Values/ | What matters to you |
| `skill` | Skills/ | Competencies, things you know or are learning |
| `habit` | Habits/ | Recurring behaviors — good and bad |
| `book` | Books/ | Books read, with takeaways |
| `interest` | Interests/ | Topics, hobbies, curiosities |
| `experience` | Experiences/ | Significant life events |
| `daily` | Daily/ | Journal entries, reflections |
| `_meta` | _meta/ | Schema docs, system config |

---

## Common Frontmatter (All Nodes)

```yaml
---
id: unique-slug-id          # Required. Lowercase, hyphenated. Must be unique across vault.
type: goal                   # Required. One of the types above.
title: Human Readable Title  # Required. Display name for the node.
created: 2026-02-23          # Required. ISO date.
updated: 2026-02-23          # Required. ISO date. Updated on every edit.
tags: [optional, tags]       # Optional. Freeform tags for extra categorization.
---
```

---

## Type-Specific Frontmatter

### Goal
```yaml
status: active          # active | achieved | paused | abandoned
priority: high          # high | medium | low
deadline: 2026-06-01    # Optional. ISO date.
progress: 30            # Optional. 0-100 percentage.
```

### Fear
```yaml
intensity: 7            # 1-10 scale
rationality: partially-rational  # rational | partially-rational | irrational
status: active          # active | overcome | managed
```

### Person
```yaml
relationship: mentor    # mentor | friend | family | colleague
frequency: weekly       # daily | weekly | monthly | rare
```

### Book
```yaml
author: James Clear
status: finished        # reading | finished | abandoned
rating: 5              # 1-5
```

### Habit
```yaml
frequency: daily        # daily | weekly
status: active          # active | lapsed | building
streak: 14              # Optional. Current streak count.
```

### Belief
```yaml
confidence: 8           # 1-10 scale
domain: career          # career | relationships | identity | world
```

### Value
```yaml
priority: core          # core | important | aspirational
```

### Skill
```yaml
level: intermediate     # beginner | intermediate | advanced | expert
actively_learning: true # true | false
```

### Interest
```yaml
# No required type-specific fields. Use tags for categorization.
```

### Experience
```yaml
date: 2025-06-15        # When it happened. ISO date.
impact: high            # high | medium | low. How much it shaped you.
```

### Daily
```yaml
date: 2026-02-23        # The day this entry is for. ISO date.
mood: good              # Optional. great | good | neutral | bad | terrible
energy: high            # Optional. high | medium | low
```

---

## Relationships (Edges)

Relationships are encoded via `[[wikilinks]]` placed under specific section headings in the markdown body.

| Section Heading | Edge Type | Meaning | Example |
|----------------|-----------|---------|---------|
| `## Blockers` | `blocked_by` | This node is blocked by the linked node | A goal blocked by a fear |
| `## Supports` | `supported_by` | The linked node supports this node | A habit supporting a goal |
| `## Related` | `relates_to` | General association | Two skills that complement each other |
| `## Contradicts` | `contradicts` | The linked node contradicts this node | A belief contradicting a value |
| `## Inspired By` | `inspired_by` | This node was inspired by the linked node | A goal inspired by a book |
| `## People` | `involves` | A person is involved with this node | A goal involving a mentor |
| *(outside any section)* | `relates_to` | Default fallback | Any wikilink not under a heading |

### Rules
- Wikilinks use the node's `id` field: `[[public-speaking-fear]]`
- A wikilink can optionally include annotation text after a dash: `[[public-speaking-fear]] — I freeze up in meetings`
- The section heading determines the edge type. Links outside any `##` section default to `relates_to`.
- Edges are directional: the file containing the link is the source, the linked node is the target.

---

## ID Conventions

- IDs are lowercase, hyphen-separated slugs: `get-promoted`, `public-speaking-fear`, `mentor-sarah`
- IDs must be unique across the entire vault (not just within a folder)
- The filename matches the ID: `get-promoted.md`
- IDs should be descriptive but concise

---

## File Naming

- Filename = `{id}.md`
- Files live in the folder matching their type: `Goals/get-promoted.md`
- Daily entries use date-based IDs: `Daily/2026-02-23.md`
