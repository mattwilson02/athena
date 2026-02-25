---
id: schema
type: _meta
title: Athena Vault Schema
created: 2026-02-23
updated: 2026-02-25
version: 2
---

# Athena Vault Schema — V2

This document defines the ontology, domains, types, frontmatter schema, and relationship conventions for the Athena knowledge graph. It is the single source of truth — the backend parses this file at boot to generate system prompts, folder mappings, and validation rules.

---

## Domains

<!-- PARSER:DOMAINS_START -->
| Domain | Folder | Description |
|--------|--------|-------------|
| Self | Self | Inner world — goals, fears, beliefs, values, habits, skills |
| People | People | Your network — individuals and organisations |
| Knowledge | Knowledge | Things you've learned or want to remember |
| Life | Life | What's happened and what you're doing |
| Planning | Planning | Getting things done — tasks, projects, events |
| Places | Places | Geography — cities, restaurants, landmarks |
| Finance | Finance | Money — expenses, subscriptions, budgets |
<!-- PARSER:DOMAINS_END -->

---

## Node Types

<!-- PARSER:TYPES_START -->

### goal
- **Domain:** Self
- **Folder:** Self/Goals
- **Description:** Something you're actively working toward or want to achieve.
- **Frontmatter:**
```yaml
status: active          # active | achieved | paused | abandoned
priority: medium        # high | medium | low
deadline:               # Optional. ISO date.
progress: 0             # Optional. 0-100 percentage.
```

### fear
- **Domain:** Self
- **Folder:** Self/Fears
- **Description:** Something holding you back or causing anxiety.
- **Frontmatter:**
```yaml
intensity: 5            # 1-10 scale.
rationality: partially-rational  # rational | partially-rational | irrational
status: active          # active | overcome | managed
```

### belief
- **Domain:** Self
- **Folder:** Self/Beliefs
- **Description:** A core conviction or mental model you hold.
- **Frontmatter:**
```yaml
confidence: 5           # 1-10 scale.
domain:                 # Optional. career | relationships | identity | world
```

### value
- **Domain:** Self
- **Folder:** Self/Values
- **Description:** Something you care deeply about — a principle you live by.
- **Frontmatter:**
```yaml
priority: core          # core | important | aspirational
```

### habit
- **Domain:** Self
- **Folder:** Self/Habits
- **Description:** A recurring behavior — good or bad.
- **Frontmatter:**
```yaml
frequency: daily        # daily | weekly | monthly
status: active          # active | lapsed | building
streak: 0              # Optional. Current streak count.
```

### skill
- **Domain:** Self
- **Folder:** Self/Skills
- **Description:** A competency you have or are building.
- **Frontmatter:**
```yaml
level: beginner         # beginner | intermediate | advanced | expert
actively_learning: true # true | false
```

### person
- **Domain:** People
- **Folder:** People/Persons
- **Description:** A specific individual — friend, mentor, family, colleague, anyone you know.
- **Frontmatter:**
```yaml
relationship: friend    # mentor | friend | family | colleague | acquaintance | partner
frequency: monthly      # daily | weekly | monthly | rare | inactive
met_through:            # Optional. How you met.
company:                # Optional. Where they work.
location:               # Optional. City or region.
```

### organisation
- **Domain:** People
- **Folder:** People/Organisations
- **Description:** A company, institution, or group.
- **Frontmatter:**
```yaml
industry:               # Optional. e.g. "fintech", "education"
role:                   # Optional. Your relationship — "employer", "client", "partner"
status: active          # active | past | prospective
```

### book
- **Domain:** Knowledge
- **Folder:** Knowledge/Books
- **Description:** A book you've read, are reading, or want to read.
- **Frontmatter:**
```yaml
author:                 # Required. Author name.
status: finished        # reading | finished | abandoned | to-read
rating:                 # Optional. 1-5.
```

### article
- **Domain:** Knowledge
- **Folder:** Knowledge/Articles
- **Description:** An article, blog post, or online resource worth remembering.
- **Frontmatter:**
```yaml
author:                 # Optional.
source:                 # Optional. URL or publication name.
status: read            # read | to-read | reference
```

### idea
- **Domain:** Knowledge
- **Folder:** Knowledge/Ideas
- **Description:** A thought, concept, or hypothesis you want to capture.
- **Frontmatter:**
```yaml
status: raw             # raw | developing | validated | archived
domain:                 # Optional. What area this idea relates to.
```

### note
- **Domain:** Knowledge
- **Folder:** Knowledge/Notes
- **Description:** Freeform catch-all for anything that doesn't fit elsewhere. Use tags for categorisation.
- **Frontmatter:**
```yaml
# No required type-specific fields.
```

### interest
- **Domain:** Knowledge
- **Folder:** Knowledge/Interests
- **Description:** A topic, hobby, or curiosity you want to track.
- **Frontmatter:**
```yaml
# No required type-specific fields. Use tags.
```

### experience
- **Domain:** Life
- **Folder:** Life/Experiences
- **Description:** A specific past event or life chapter. Use ONLY for discrete things that happened — not ongoing relationships, recurring patterns, or current pursuits.
- **Frontmatter:**
```yaml
date:                   # When it happened. ISO date.
impact: medium          # high | medium | low
```

### daily
- **Domain:** Life
- **Folder:** Life/Daily
- **Description:** A journal entry for a specific day.
- **Frontmatter:**
```yaml
date:                   # The day this entry is for. ISO date.
mood:                   # Optional. great | good | neutral | bad | terrible
energy:                 # Optional. high | medium | low
```

### memory
- **Domain:** Life
- **Folder:** Life/Memories
- **Description:** A personal memory worth preserving — more vivid or emotional than an experience.
- **Frontmatter:**
```yaml
date:                   # Optional. When it happened. ISO date.
people: []              # Optional. Person node IDs involved.
place:                  # Optional. Place node ID.
mood:                   # Optional. How you felt.
```

### task
- **Domain:** Planning
- **Folder:** Planning/Tasks
- **Description:** Something you need to do. A concrete action item.
- **Frontmatter:**
```yaml
status: todo            # todo | in-progress | done | cancelled
priority: medium        # high | medium | low
due:                    # Optional. ISO date.
project:                # Optional. Project node ID this belongs to.
```

### project
- **Domain:** Planning
- **Folder:** Planning/Projects
- **Description:** A multi-step initiative grouping related tasks and goals.
- **Frontmatter:**
```yaml
status: active          # active | paused | completed | cancelled
priority: medium        # high | medium | low
deadline:               # Optional. ISO date.
progress: 0             # Optional. 0-100 percentage.
```

### reminder
- **Domain:** Planning
- **Folder:** Planning/Reminders
- **Description:** Something you need to remember at a specific time.
- **Frontmatter:**
```yaml
due:                    # ISO date or datetime.
status: pending         # pending | done | dismissed
recurring:              # Optional. daily | weekly | monthly | yearly
```

### event
- **Domain:** Planning
- **Folder:** Planning/Events
- **Description:** A scheduled occurrence — race, meeting, trip, appointment.
- **Frontmatter:**
```yaml
date:                   # ISO date or datetime.
location:               # Optional. Place node ID or freeform string.
people: []              # Optional. Person node IDs.
status: upcoming        # upcoming | attended | cancelled
```

### place
- **Domain:** Places
- **Folder:** Places
- **Description:** A location — city, restaurant, bar, landmark, neighbourhood.
- **Frontmatter:**
```yaml
category: other         # restaurant | city | country | bar | cafe | hotel | landmark | neighbourhood | region | other
location:               # Optional. Parent location, city, or country.
visited: false          # true | false
rating:                 # Optional. 1-5. Only if visited.
```

### expense
- **Domain:** Finance
- **Folder:** Finance/Expenses
- **Description:** A one-off or recurring cost.
- **Frontmatter:**
```yaml
amount:                 # Required. Numeric.
currency: GBP           # ISO currency code.
date:                   # ISO date.
category: other         # food | transport | housing | entertainment | health | education | travel | other
recurring: false        # true | false
```

### subscription
- **Domain:** Finance
- **Folder:** Finance/Subscriptions
- **Description:** A recurring payment for a service.
- **Frontmatter:**
```yaml
amount:                 # Required. Numeric.
currency: GBP           # ISO currency code.
frequency: monthly      # monthly | yearly | weekly
category: other         # software | media | fitness | food | other
status: active          # active | cancelled | paused
renewal_date:           # Optional. ISO date.
```

### budget
- **Domain:** Finance
- **Folder:** Finance/Budgets
- **Description:** A spending target for a period and category.
- **Frontmatter:**
```yaml
period: monthly         # monthly | weekly | yearly
amount:                 # Required. Target spend.
currency: GBP           # ISO currency code.
category:               # Optional. Matches expense categories. Omit for overall budget.
```

<!-- PARSER:TYPES_END -->

---

## Common Frontmatter (All Nodes)

Every node has these fields regardless of type:

```yaml
---
id: unique-slug-id          # Required. Lowercase, hyphenated. Unique across vault.
type: goal                   # Required. One of the types above.
title: Human Readable Title  # Required. Display name.
created: 2026-02-25          # Required. ISO date.
updated: 2026-02-25          # Required. ISO date.
tags: []                     # Optional. Freeform tags.
---
```

---

## Relationships (Edges)

Relationships are encoded via `[[wikilinks]]` under section headings in the markdown body.

<!-- PARSER:EDGES_START -->
| Section Heading | Edge Type | Meaning | Example |
|----------------|-----------|---------|---------|
| `## Blockers` | `blocked_by` | This node is blocked by the linked node | Goal blocked by a fear |
| `## Supports` | `supported_by` | The linked node supports this node | Habit supporting a goal |
| `## Related` | `relates_to` | General association | Two skills that complement each other |
| `## Contradicts` | `contradicts` | Contradictory relationship | Belief contradicting a value |
| `## Inspired By` | `inspired_by` | This node was inspired by the linked node | Goal inspired by a book |
| `## People` | `involves` | A person is involved | Goal involving a mentor |
| `## Part Of` | `part_of` | This node belongs to a larger node | Task part of a project |
| `## Located In` | `located_in` | This node is at a place | Event at a place |
| `## Funded By` | `funded_by` | Financial relationship | Subscription funded by budget |
| `## Met At` | `met_at` | Person met at a place or event | Person met at a conference |
| *(outside any section)* | `relates_to` | Default fallback | Any wikilink not under a heading |
<!-- PARSER:EDGES_END -->

### Rules
- Wikilinks use the node's `id` field: `[[public-speaking-fear]]`
- A wikilink can include annotation: `[[public-speaking-fear]] — I freeze up in meetings`
- The section heading determines the edge type. Links outside any `##` section default to `relates_to`.
- Edges are directional: the file containing the link is the source, the linked node is the target.

---

## ID Conventions

- Lowercase, hyphen-separated slugs: `get-promoted`, `jake-smith`, `lake-district`
- Unique across the entire vault (not just within a folder)
- Filename matches ID: `get-promoted.md`
- Descriptive but concise

---

## File Naming

- Filename = `{id}.md`
- Files live in the folder matching their type (see type definitions above)
- Daily entries use date-based IDs: `Life/Daily/2026-02-25.md`
