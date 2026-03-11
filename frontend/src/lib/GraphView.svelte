<script>
  import { getGraph, getNode } from './api.js';
  import { createForceLayout } from './graph/useForceLayout.svelte.js';
  import GraphScene from './graph/GraphScene.svelte';
  import NodeDetail from './NodeDetail.svelte';
  import * as THREE from 'three';

  let { schema = null, filter = null, selectedNode = null } = $props();

  let selectedId = $state(null);
  let hoveredId = $state(null);
  let selectedNodeDetail = $state(null);
  let selectedNeighbors = $state([]);
  let isEmpty = $state(false);
  let layoutData = $state(null);
  let containerEl = $state(null);

  // Non-reactive — mutated directly like the old graph
  let cameraRef = null;
  let allNodes = [];
  let allEdges = [];

  function getTypesForDomain(domain) {
    if (!schema) return [];
    return schema.domains?.[domain]?.types || [];
  }

  function computeLayout(nodes, edges, currentFilter) {
    let filteredNodes = nodes;
    if (currentFilter?.type) {
      filteredNodes = nodes.filter((n) => n.type === currentFilter.type);
    } else if (currentFilter?.domain) {
      const domainTypes = new Set(getTypesForDomain(currentFilter.domain));
      filteredNodes = nodes.filter((n) => domainTypes.has(n.type));
    }

    if (filteredNodes.length === 0) {
      isEmpty = true;
      layoutData = null;
      return;
    }

    const filteredIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = edges.filter(
      (e) => filteredIds.has(e.source) && filteredIds.has(e.target),
    );

    isEmpty = false;
    layoutData = createForceLayout(filteredNodes, filteredEdges, schema);
  }

  $effect(() => {
    // Read filter outside the conditional so Svelte always tracks it as a dependency
    const currentFilter = filter;
    if (allNodes.length > 0) {
      computeLayout(allNodes, allEdges, currentFilter);
    }
  });

  async function loadGraph() {
    try {
      const data = await getGraph();
      allNodes = data.nodes || [];
      allEdges = data.edges || [];
      if (!allNodes.length) { isEmpty = true; return; }
      computeLayout(allNodes, allEdges, filter);
    } catch { isEmpty = true; }
  }

  async function selectNode(nodeId) {
    if (!nodeId) {
      selectedId = null;
      selectedNodeDetail = null;
      selectedNeighbors = [];
      return;
    }
    selectedId = nodeId;
    try {
      const data = await getNode(nodeId);
      selectedNodeDetail = data.node;
      selectedNeighbors = data.neighbors;
    } catch { selectedNodeDetail = null; }
  }

  function closeDetail() {
    selectedNodeDetail = null;
    selectedNeighbors = [];
    selectedId = null;
  }

  // --- Hit testing: project 3D → screen, find nearest node to cursor ---
  const vec3 = new THREE.Vector3();

  function findNodeAt(clientX, clientY) {
    if (!cameraRef || !layoutData) return null;
    const canvas = containerEl?.querySelector('canvas');
    if (!canvas) return null;

    const rect = canvas.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;

    const { simNodes, positions, sizes } = layoutData;
    let bestIdx = -1;
    let bestDist = Infinity;

    for (let i = 0; i < simNodes.length; i++) {
      vec3.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
      vec3.project(cameraRef);
      if (vec3.z > 1) continue;

      const sx = (vec3.x * 0.5 + 0.5) * rect.width;
      const sy = (-vec3.y * 0.5 + 0.5) * rect.height;
      const dist = Math.hypot(px - sx, py - sy);
      const hitRadius = Math.max(sizes[i] * 10, 14);

      if (dist < hitRadius && dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }

    return bestIdx >= 0 ? simNodes[bestIdx] : null;
  }

  // --- Event handlers on the container div ---
  let mouseDownPos = { x: 0, y: 0 };

  function handleMouseDown(e) {
    mouseDownPos = { x: e.clientX, y: e.clientY };
  }

  function handleClick(e) {
    // Suppress click if the mouse moved (orbit drag)
    const dx = Math.abs(e.clientX - mouseDownPos.x);
    const dy = Math.abs(e.clientY - mouseDownPos.y);
    if (dx > 5 || dy > 5) return;

    const node = findNodeAt(e.clientX, e.clientY);
    if (node) {
      selectNode(node.id);
    }
    // Don't deselect on empty click — only close via NodeDetail's X button
  }

  function handleMouseMove(e) {
    if (e.buttons > 0) return;
    const node = findNodeAt(e.clientX, e.clientY);
    hoveredId = node ? node.id : null;
    const canvas = containerEl?.querySelector('canvas');
    if (canvas) canvas.style.cursor = node ? 'pointer' : 'grab';
  }

  // Camera ref callback from GraphScene
  function handleCameraReady(camera) {
    cameraRef = camera;
  }

  $effect(() => { if (selectedNode) selectNode(selectedNode); });
  $effect(() => { loadGraph(); });
</script>

<div
  class="graph-view"
  bind:this={containerEl}
  onmousedown={handleMouseDown}
  onclick={handleClick}
  onmousemove={handleMouseMove}
  role="presentation"
>
  {#if isEmpty}
    <div class="empty-state">
      <div class="empty-icon">
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
          <circle cx="16" cy="20" r="4" stroke="currentColor" stroke-width="1.5" opacity="0.4"/>
          <circle cx="32" cy="16" r="3" stroke="currentColor" stroke-width="1.5" opacity="0.3"/>
          <circle cx="28" cy="34" r="5" stroke="currentColor" stroke-width="1.5" opacity="0.5"/>
        </svg>
      </div>
      <h3>No nodes yet</h3>
      <p>Start a conversation and I'll map it out.</p>
    </div>
  {:else if layoutData}
    <GraphScene
      simNodes={layoutData.simNodes}
      positions={layoutData.positions}
      sizes={layoutData.sizes}
      edgeIndices={layoutData.edgeIndices}
      nodeIndexById={layoutData.nodeIndexById}
      {selectedId}
      {hoveredId}
      onCameraReady={handleCameraReady}
    />
  {/if}

  {#if selectedNodeDetail}
    <NodeDetail
      node={selectedNodeDetail}
      neighbors={selectedNeighbors}
      {schema}
      onClose={closeDetail}
      onNodeClick={(id) => selectNode(id)}
    />
  {/if}
</div>

<style>
  .graph-view {
    width: 100%;
    height: 100%;
    position: relative;
    overflow: hidden;
    background: #0a0a14;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-secondary);
    gap: var(--space-sm);
  }

  .empty-icon { color: var(--text-muted); margin-bottom: var(--space-sm); }
  .empty-state h3 { color: var(--text-primary); font-size: var(--text-lg); font-weight: 600; }
  .empty-state p { font-size: var(--text-sm); color: var(--text-muted); }
</style>
