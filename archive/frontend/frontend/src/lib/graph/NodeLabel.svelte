<script>
  import { T } from '@threlte/core';
  import { HTML } from '@threlte/extras';
  import { getTypeColor } from '../colors.js';

  let {
    simNodes = [],
    positions,
    sizes,
    hoveredId = null,
    selectedId = null,
    nodeIndexById = {},
  } = $props();

  // Only show labels for hovered/selected nodes
  const visibleLabels = $derived.by(() => {
    const labels = [];
    const ids = [hoveredId, selectedId].filter(Boolean);
    for (const id of ids) {
      const idx = nodeIndexById[id];
      if (idx === undefined) continue;
      const node = simNodes[idx];
      if (!node) continue;
      labels.push({
        id: node.id,
        title: node.title || node.id,
        type: node.type,
        x: positions[idx * 3],
        y: positions[idx * 3 + 1] + sizes[idx] * 1.8 + 2,
        z: positions[idx * 3 + 2],
        color: getTypeColor(node.type),
      });
    }
    // Deduplicate (if hovered === selected)
    const seen = new Set();
    return labels.filter((l) => {
      if (seen.has(l.id)) return false;
      seen.add(l.id);
      return true;
    });
  });
</script>

{#each visibleLabels as label (label.id)}
  <T.Group position={[label.x, label.y, label.z]}>
    <HTML center pointerEvents="none">
      <div class="node-label">
        <div class="label-title">{label.title}</div>
        <div class="label-type" style="color: {label.color}">{label.type}</div>
      </div>
    </HTML>
  </T.Group>
{/each}

<style>
  .node-label {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    white-space: nowrap;
    pointer-events: none;
    user-select: none;
  }

  .label-title {
    font-size: 15px;
    font-weight: 700;
    color: #ffffff;
    text-shadow:
      0 0 6px rgba(0, 0, 0, 0.9),
      0 0 12px rgba(0, 0, 0, 0.7),
      0 1px 3px rgba(0, 0, 0, 0.8);
    letter-spacing: 0.3px;
  }

  .label-type {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    text-shadow:
      0 0 6px rgba(0, 0, 0, 0.9),
      0 0 12px rgba(0, 0, 0, 0.7);
  }
</style>
