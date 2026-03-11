# Spec: 3D Graph View

**Epoch:** E2 — Conversations That Stick
**Item:** 7 (bonus)
**Files:** `frontend/src/lib/GraphView.svelte` (rewrite), new: `frontend/src/lib/graph/` directory
**Status:** Specced

---

## Problem

The current 2D canvas graph is unusable at scale. 137 nodes collapse into a single overlapping blob — labels overlap, no spatial structure, no way to explore clusters. It's a tech demo, not a tool.

## Vision

A **cosmic 3D knowledge graph** — nodes as glowing points in space, clusters with visible structure, edges as subtle connecting lines. Think constellation map, not org chart. The user orbits through their knowledge like navigating a star system.

## Tech Stack

| Dep | Purpose |
|-----|---------|
| `three` | 3D rendering engine |
| `@threlte/core` | Svelte 5 bindings for Three.js |
| `@threlte/extras` | OrbitControls, Text, helpers |
| `d3-force-3d` | 3D force-directed layout (extends d3-force to xyz) |

## Visual Design

### Aesthetic: "Constellation"

- **Background:** Deep dark (#0a0a14 → #0f0f1a gradient), subtle star-field particles at varying depths
- **Nodes:** Small glowing spheres, color-coded by type (existing `colors.js` palette)
  - Base size: small (radius ~0.3–0.5), scaled by connection count
  - Glow/bloom effect — nodes emit soft light matching their type color
  - Hover: brighten + scale up 1.5x + show label
  - Selected: bright ring + connected neighbors highlighted
- **Edges:** Thin translucent lines (opacity 0.06–0.12), brighter on hover/select
  - No arrows — direction isn't important for visual exploration
- **Labels:** Hidden by default. Shown on hover (single node) or when zoomed in close
  - Billboard text (always faces camera)
  - Small, clean, semi-transparent background pill
  - Only render labels for nodes within a distance threshold of camera (LOD)
- **Clusters:** No random force spaghetti. Intentional radial layout (see below).

### Spatial Layout: "Solar System"

The graph has a deliberate structure — **Self at the center, everything else orbits outward by importance.**

**Core principle:** Distance from center = distance from identity. Your values, goals, and fears are the gravitational core. Everything else exists in relation to them.

**Radial layers (inside → out):**

| Layer | Distance | What lives here | Why |
|-------|----------|-----------------|-----|
| 0 — Core | 0–15 | Self domain: values, goals, fears, beliefs | This is who you are |
| 1 — Inner orbit | 15–40 | People, habits, skills | Directly tied to identity |
| 2 — Middle orbit | 40–80 | Projects, events, experiences, memories | Active life structure |
| 3 — Outer orbit | 80–130 | Knowledge (books, ideas, notes), places | Reference material |
| 4 — Periphery | 130–180 | Finance (expenses, subscriptions), daily logs, reminders | Transactional, low-permanence |

**Within each layer**, nodes are positioned by connection count — more connected nodes sit closer to center. A goal with 12 edges is closer to the core than a goal with 2.

**How this works in the force simulation:**
- Each node gets a **target radius** based on its domain + connection count
- A custom `forceRadial(targetRadius)` pulls nodes toward their assigned shell
- Standard `forceManyBody()` still provides repulsion (prevents overlap within a shell)
- `forceLink()` still attracts connected nodes (so linked nodes cluster together within their layer)
- The radial force is stronger than link attraction — structure wins over spaghetti

**Visual result:** From the default camera angle, you see concentric shells of colored light. Self (greens/pinks) glowing at the center. People (blues) in the first ring. Projects/tasks (sky blues) in the middle. Knowledge (yellows) further out. Finance (oranges) at the edge. Edges trace paths from outer nodes inward toward the core — everything connects back to Self.

### Camera & Controls

- **OrbitControls:** click-drag to rotate, scroll to zoom, right-drag to pan
- **Fly-to:** clicking a node smoothly animates the camera to orbit around it
- **Auto-rotate:** very slow idle rotation (0.1 deg/s) when not interacting, stops on input
- **Initial view:** camera positioned to see the full graph, slightly angled (not top-down)
- **Zoom limits:** min distance 5 (close enough to read labels), max distance 300 (full overview)

### Interaction

| Action | Result |
|--------|--------|
| Hover node | Brighten + scale up + show label tooltip |
| Click node | Select → fly-to + highlight neighbors + show NodeDetail panel |
| Click empty space | Deselect |
| Scroll | Zoom in/out |
| Drag | Orbit rotate |
| Right-drag | Pan |
| Double-click node | Zoom in tight on that node |

### Selection & Highlight

When a node is selected:
1. Selected node: full brightness, scale 2x, bright ring
2. 1-hop neighbors: 70% brightness
3. Connected edges: 40% opacity (vs 8% default)
4. Everything else: dim to 10% opacity
5. NodeDetail slide-in panel (existing component, unchanged)

### Performance

- **InstancedMesh** for all nodes (single draw call)
- **LineSegments** for all edges (single draw call)
- **Sprite labels** only for visible/hovered nodes (not all 137)
- **d3-force-3d** simulation runs for ~300 ticks on load, then settles
  - `forceRadial(targetRadius)` — primary force, pulls each node toward its domain shell
  - `forceLink()` — secondary, attracts connected nodes (weaker than radial)
  - `forceManyBody()` — repulsion within shells (charge: -60)
  - `forceCollide()` — prevent overlap
  - No `forceCenter()` — radial force handles centering implicitly
  - Target radius per node: `DOMAIN_RADIUS[domain] - (connections * 1.5)` (more edges = closer to core within layer)
- Force sim runs in `requestAnimationFrame`, updates InstancedMesh matrices per tick
- After settling, simulation stops (no CPU burn at idle)

### Filtering

Same filtering interface as current (domain/type from Sidebar):
- When a filter is applied, non-matching nodes fade to 5% opacity (not removed)
- Matching nodes stay full brightness
- Edges between non-matching nodes also fade
- This preserves spatial context — you can see where the filtered subset lives in the full graph

### External Node Selection

When navigating from chat (node chips) or search (Cmd+K):
- Camera flies to the selected node
- Node gets selected state
- NodeDetail panel opens

## File Structure

```
frontend/src/lib/graph/
├── GraphScene.svelte      # Threlte <Canvas> + scene setup (lights, controls, background)
├── NodeCloud.svelte        # InstancedMesh for all nodes + interaction (hover/click)
├── EdgeLines.svelte        # LineSegments geometry for all edges
├── NodeLabel.svelte        # Billboard text label (shown on hover/select)
├── StarField.svelte        # Background particle system (decorative)
├── useForceLayout.svelte.js  # Svelte 5 rune-based force simulation hook
└── layout.js                 # Domain → radius mapping, node radius calculation
```

`GraphView.svelte` becomes a thin wrapper that:
1. Fetches graph data (existing `getGraph()` + `getNode()` calls)
2. Passes nodes/edges to `<GraphScene>`
3. Handles filter prop and external node selection
4. Renders NodeDetail panel on selection

## Props Interface (unchanged)

```svelte
let { schema = null, filter = null, selectedNode = null } = $props();
```

App.svelte requires zero changes.

## What Does NOT Change

- `App.svelte` — same props, same import
- `NodeDetail.svelte` — still slide-in panel, same data
- `Sidebar.svelte` — same filter interface
- `colors.js` — same type/domain color source
- `api.js` — same `getGraph()`, `getNode()` calls
- Backend — no changes at all

## Risks

- **Threlte v8 + Svelte 5** — relatively new combo, may hit edge cases. Mitigation: Threlte team actively supports Svelte 5, good docs.
- **Mobile/trackpad** — OrbitControls work with touch but may feel different. Mitigation: test on trackpad, adjust sensitivity.
- **Label readability** — 3D labels can be hard to read at certain angles. Mitigation: billboard text (always faces camera) + LOD culling.
- **Docker image size** — Three.js adds ~600KB gzipped to the frontend bundle. Acceptable for the UX gain.

## Out of Scope

- Legend/key overlay (not needed — Sidebar already shows domain/type breakdown)
- Wireframe cluster hulls (reference image has them but they add visual noise for personal-scale graphs)
- Edge labels/types (edges are too numerous, keep them minimal)
- Node drag in 3D (complex interaction, orbit is sufficient)
- VR/AR support
