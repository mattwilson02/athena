<script>
  import { getGraph, getNode } from './api.js';
  import NodeDetail from './NodeDetail.svelte';

  const TYPE_COLORS = {
    goal: '#4ade80', fear: '#f87171', person: '#60a5fa', book: '#fbbf24',
    skill: '#a78bfa', habit: '#34d399', value: '#f472b6', belief: '#fb923c',
    interest: '#22d3ee', experience: '#e879f9', daily: '#94a3b8',
  };

  let canvasEl = $state(null);
  let container = $state(null);
  let selectedNodeDetail = $state(null);
  let selectedNeighbors = $state([]);
  let isEmpty = $state(false);

  // Simulation state (mutated directly for performance, not reactive)
  let simNodes = [];
  let simEdges = [];
  let nodeMap = {};
  let selectedId = null;
  let dragNode = null;
  let isDragging = false;
  let isPanning = false;
  let panOffset = { x: 0, y: 0 };
  let zoom = 1;
  let lastMouse = { x: 0, y: 0 };
  let animId = null;

  // Force simulation constants
  const REPULSION = 800;
  const ATTRACTION = 0.008;
  const CENTER_GRAVITY = 0.012;
  const DAMPING = 0.88;

  function tick() {
    const len = simNodes.length;
    if (len === 0) return;

    // Repulsion between all pairs
    for (let i = 0; i < len; i++) {
      for (let j = i + 1; j < len; j++) {
        const a = simNodes[i], b = simNodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let dist = Math.sqrt(dx * dx + dy * dy) || 1;
        let force = REPULSION / (dist * dist);
        let fx = (dx / dist) * force;
        let fy = (dy / dist) * force;
        a.vx -= fx; a.vy -= fy;
        b.vx += fx; b.vy += fy;
      }
    }

    // Attraction along edges
    for (const edge of simEdges) {
      let dx = edge.target.x - edge.source.x;
      let dy = edge.target.y - edge.source.y;
      let dist = Math.sqrt(dx * dx + dy * dy) || 1;
      let force = dist * ATTRACTION;
      let fx = (dx / dist) * force;
      let fy = (dy / dist) * force;
      edge.source.vx += fx; edge.source.vy += fy;
      edge.target.vx -= fx; edge.target.vy -= fy;
    }

    // Center gravity
    const cx = (canvasEl?.width || 800) / 2 / zoom - panOffset.x / zoom;
    const cy = (canvasEl?.height || 600) / 2 / zoom - panOffset.y / zoom;
    for (const node of simNodes) {
      node.vx += (cx - node.x) * CENTER_GRAVITY;
      node.vy += (cy - node.y) * CENTER_GRAVITY;
    }

    // Apply velocity
    for (const node of simNodes) {
      if (node === dragNode) continue;
      node.vx *= DAMPING;
      node.vy *= DAMPING;
      node.x += node.vx;
      node.y += node.vy;
    }
  }

  function draw() {
    if (!canvasEl) return;
    const ctx = canvasEl.getContext('2d');
    const w = canvasEl.width;
    const h = canvasEl.height;

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(panOffset.x, panOffset.y);
    ctx.scale(zoom, zoom);

    // Edges
    for (const edge of simEdges) {
      ctx.beginPath();
      ctx.moveTo(edge.source.x, edge.source.y);
      ctx.lineTo(edge.target.x, edge.target.y);

      // Highlight edges connected to selected node
      if (selectedId && (edge.source.id === selectedId || edge.target.id === selectedId)) {
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
        ctx.lineWidth = 1.5;
      } else {
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.lineWidth = 1;
      }
      ctx.stroke();
    }

    // Nodes
    for (const node of simNodes) {
      const radius = 6 + Math.min(node.connections * 2, 14);
      const isSelected = selectedId === node.id;

      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = isSelected ? '#ffffff' : (TYPE_COLORS[node.type] || '#888');
      ctx.fill();

      if (isSelected) {
        ctx.strokeStyle = TYPE_COLORS[node.type] || '#888';
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Label
      ctx.fillStyle = isSelected ? '#ffffff' : '#999';
      ctx.font = `${isSelected ? '12' : '11'}px -apple-system, sans-serif`;
      ctx.textAlign = 'center';
      ctx.fillText(node.title || node.id, node.x, node.y + radius + 14);
    }

    ctx.restore();
    tick();
    animId = requestAnimationFrame(draw);
  }

  function screenToWorld(sx, sy) {
    return {
      x: (sx - panOffset.x) / zoom,
      y: (sy - panOffset.y) / zoom,
    };
  }

  function findNodeAt(sx, sy) {
    const { x, y } = screenToWorld(sx, sy);
    for (let i = simNodes.length - 1; i >= 0; i--) {
      const node = simNodes[i];
      const radius = 6 + Math.min(node.connections * 2, 14);
      const dx = node.x - x;
      const dy = node.y - y;
      if (dx * dx + dy * dy <= (radius + 4) * (radius + 4)) {
        return node;
      }
    }
    return null;
  }

  function handleMouseDown(e) {
    const rect = canvasEl.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;

    const hit = findNodeAt(sx, sy);
    if (hit) {
      dragNode = hit;
      isDragging = false;
    } else {
      isPanning = true;
    }
    lastMouse = { x: e.clientX, y: e.clientY };
  }

  function handleMouseMove(e) {
    const dx = e.clientX - lastMouse.x;
    const dy = e.clientY - lastMouse.y;

    if (dragNode) {
      isDragging = true;
      const rect = canvasEl.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const world = screenToWorld(sx, sy);
      dragNode.x = world.x;
      dragNode.y = world.y;
      dragNode.vx = 0;
      dragNode.vy = 0;
    } else if (isPanning) {
      panOffset.x += dx;
      panOffset.y += dy;
    }

    lastMouse = { x: e.clientX, y: e.clientY };
  }

  async function handleMouseUp(e) {
    if (dragNode && !isDragging) {
      // Click (not drag) — select node
      selectedId = dragNode.id;
      try {
        const data = await getNode(dragNode.id);
        selectedNodeDetail = data.node;
        selectedNeighbors = data.neighbors;
      } catch {
        selectedNodeDetail = null;
      }
    }
    dragNode = null;
    isDragging = false;
    isPanning = false;
  }

  function handleWheel(e) {
    e.preventDefault();
    const rect = canvasEl.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const oldZoom = zoom;
    const delta = e.deltaY > 0 ? 0.92 : 1.08;
    zoom = Math.max(0.2, Math.min(3.0, zoom * delta));

    // Zoom toward cursor
    panOffset.x = mx - (mx - panOffset.x) * (zoom / oldZoom);
    panOffset.y = my - (my - panOffset.y) * (zoom / oldZoom);
  }

  function closeDetail() {
    selectedNodeDetail = null;
    selectedNeighbors = [];
    selectedId = null;
  }

  async function handleNodeClick(nodeId) {
    selectedId = nodeId;
    try {
      const data = await getNode(nodeId);
      selectedNodeDetail = data.node;
      selectedNeighbors = data.neighbors;
    } catch {
      selectedNodeDetail = null;
    }
  }

  function resizeCanvas() {
    if (!canvasEl || !container) return;
    canvasEl.width = container.clientWidth;
    canvasEl.height = container.clientHeight;
  }

  async function loadGraph() {
    try {
      const data = await getGraph();
      if (!data.nodes.length) {
        isEmpty = true;
        return;
      }
      isEmpty = false;

      const cx = 400, cy = 300;
      simNodes = data.nodes.map(n => ({
        ...n,
        x: cx + (Math.random() - 0.5) * 400,
        y: cy + (Math.random() - 0.5) * 400,
        vx: 0, vy: 0,
        connections: 0,
      }));

      nodeMap = {};
      simNodes.forEach(n => nodeMap[n.id] = n);

      simEdges = data.edges
        .filter(e => nodeMap[e.source] && nodeMap[e.target])
        .map(e => ({
          source: nodeMap[e.source],
          target: nodeMap[e.target],
          type: e.type,
        }));

      for (const e of simEdges) {
        e.source.connections++;
        e.target.connections++;
      }
    } catch {
      isEmpty = true;
    }
  }

  $effect(() => {
    loadGraph().then(() => {
      if (canvasEl && container) {
        resizeCanvas();
        animId = requestAnimationFrame(draw);
      }
    });

    const observer = new ResizeObserver(() => resizeCanvas());
    if (container) observer.observe(container);

    return () => {
      if (animId) cancelAnimationFrame(animId);
      observer.disconnect();
    };
  });
</script>

<div class="graph-view" bind:this={container}>
  {#if isEmpty}
    <div class="empty-state">
      <h3>No nodes yet</h3>
      <p>Add some nodes to your vault and they'll appear here.</p>
    </div>
  {:else}
    <canvas
      bind:this={canvasEl}
      onmousedown={handleMouseDown}
      onmousemove={handleMouseMove}
      onmouseup={handleMouseUp}
      onmouseleave={handleMouseUp}
      onwheel={handleWheel}
    ></canvas>
  {/if}

  {#if selectedNodeDetail}
    <NodeDetail
      node={selectedNodeDetail}
      neighbors={selectedNeighbors}
      onClose={closeDetail}
      onNodeClick={handleNodeClick}
    />
  {/if}
</div>

<style>
  .graph-view {
    width: 100%;
    height: 100%;
    position: relative;
    overflow: hidden;
    background: var(--bg-primary);
  }

  canvas {
    display: block;
    cursor: grab;
  }

  canvas:active {
    cursor: grabbing;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-secondary);
    gap: 8px;
  }

  .empty-state h3 {
    color: var(--text-primary);
    font-size: 18px;
  }
</style>
