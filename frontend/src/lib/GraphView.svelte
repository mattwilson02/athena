<script>
  import { getGraph, getNode } from './api.js';
  import { getTypeColor, getDomainColor } from './colors.js';
  import NodeDetail from './NodeDetail.svelte';

  let { schema = null, filter = null, selectedNode = null } = $props();

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
  let hoveredNode = null;
  let dragNode = null;
  let isDragging = false;
  let isPanning = false;
  let panOffset = { x: 0, y: 0 };
  let zoom = 1;
  let lastMouse = { x: 0, y: 0 };
  let animId = null;
  let allNodes = [];
  let allEdges = [];
  let settled = false;
  let tickCount = 0;

  // Physics constants
  const REPULSION = 1200;
  const ATTRACTION = 0.005;
  const CENTER_GRAVITY = 0.01;
  const DAMPING = 0.85;
  const MIN_VELOCITY = 0.01;
  const COLLISION_RADIUS = 8;

  // Visual constants
  const NODE_BASE_RADIUS = 5;
  const NODE_SCALE_FACTOR = 1.8;
  const NODE_MAX_EXTRA = 16;
  const LABEL_ZOOM_THRESHOLD = 0.4;
  const LABEL_MAX_CHARS = 22;

  // Cached gradient/glow for performance
  let dpr = 1;

  function getTypesForDomain(domain) {
    if (!schema) return [];
    return schema.domains?.[domain]?.types || [];
  }

  function nodeRadius(node) {
    return NODE_BASE_RADIUS + Math.min(node.connections * NODE_SCALE_FACTOR, NODE_MAX_EXTRA);
  }

  function applyFilter() {
    let filteredNodes = allNodes;
    if (filter?.type) {
      filteredNodes = allNodes.filter(n => n.type === filter.type);
    } else if (filter?.domain) {
      const domainTypes = new Set(getTypesForDomain(filter.domain));
      filteredNodes = allNodes.filter(n => domainTypes.has(n.type));
    }

    const filteredIds = new Set(filteredNodes.map(n => n.id));
    const cx = (canvasEl?.width || 800) / (2 * dpr);
    const cy = (canvasEl?.height || 600) / (2 * dpr);

    // Arrange nodes in a circle initially for a cleaner start
    const count = filteredNodes.length;
    const baseRadius = Math.min(cx, cy) * 0.6;
    simNodes = filteredNodes.map((n, i) => {
      const angle = (i / count) * Math.PI * 2;
      const jitter = (Math.random() - 0.5) * 60;
      return {
        ...n,
        x: cx + Math.cos(angle) * (baseRadius + jitter),
        y: cy + Math.sin(angle) * (baseRadius + jitter),
        vx: 0, vy: 0,
        connections: 0,
      };
    });

    nodeMap = {};
    simNodes.forEach(n => nodeMap[n.id] = n);

    simEdges = allEdges
      .filter(e => filteredIds.has(e.source) && filteredIds.has(e.target))
      .filter(e => nodeMap[e.source] && nodeMap[e.target])
      .map(e => ({ source: nodeMap[e.source], target: nodeMap[e.target], type: e.type }));

    for (const e of simEdges) {
      e.source.connections++;
      e.target.connections++;
    }

    settled = false;
    tickCount = 0;
    isEmpty = simNodes.length === 0;
  }

  $effect(() => {
    filter;
    if (allNodes.length > 0) applyFilter();
  });

  function tick() {
    const len = simNodes.length;
    if (len === 0 || settled) return;

    // Repulsion (Barnes-Hut would be better for >500 nodes, but this is fine for personal graphs)
    for (let i = 0; i < len; i++) {
      for (let j = i + 1; j < len; j++) {
        const a = simNodes[i], b = simNodes[j];
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let dist = Math.sqrt(dx * dx + dy * dy) || 1;
        // Collision: push apart if overlapping
        const minDist = nodeRadius(a) + nodeRadius(b) + COLLISION_RADIUS;
        const effectiveDist = Math.max(dist, minDist * 0.5);
        let force = REPULSION / (effectiveDist * effectiveDist);
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
    const cx = (canvasEl?.width || 800) / (2 * dpr * zoom) - panOffset.x / zoom;
    const cy = (canvasEl?.height || 600) / (2 * dpr * zoom) - panOffset.y / zoom;
    for (const node of simNodes) {
      node.vx += (cx - node.x) * CENTER_GRAVITY;
      node.vy += (cy - node.y) * CENTER_GRAVITY;
    }

    // Integrate
    let totalVelocity = 0;
    for (const node of simNodes) {
      if (node === dragNode) continue;
      node.vx *= DAMPING;
      node.vy *= DAMPING;
      node.x += node.vx;
      node.y += node.vy;
      totalVelocity += Math.abs(node.vx) + Math.abs(node.vy);
    }

    tickCount++;
    if (tickCount > 300 && totalVelocity / len < MIN_VELOCITY) {
      settled = true;
    }
  }

  function draw() {
    if (!canvasEl) return;
    const ctx = canvasEl.getContext('2d');
    const w = canvasEl.width;
    const h = canvasEl.height;
    if (w === 0 || h === 0) { animId = requestAnimationFrame(draw); return; }

    try { drawFrame(ctx, w, h); } catch (e) { console.error('GraphView draw error:', e); }
    tick();
    animId = requestAnimationFrame(draw);
  }

  function drawFrame(ctx, w, h) {
    ctx.clearRect(0, 0, w, h);

    // Background dots pattern for depth
    drawBackgroundDots(ctx, w, h);

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.translate(panOffset.x, panOffset.y);
    ctx.scale(zoom, zoom);

    // Build sets for highlight logic
    const connectedToSelected = new Set();
    const connectedToHovered = new Set();
    if (selectedId) {
      for (const e of simEdges) {
        if (e.source.id === selectedId) connectedToSelected.add(e.target.id);
        if (e.target.id === selectedId) connectedToSelected.add(e.source.id);
      }
    }
    if (hoveredNode && hoveredNode.id !== selectedId) {
      for (const e of simEdges) {
        if (e.source.id === hoveredNode.id) connectedToHovered.add(e.target.id);
        if (e.target.id === hoveredNode.id) connectedToHovered.add(e.source.id);
      }
    }

    const hasSelection = !!selectedId;
    const hasHover = !!hoveredNode;

    // ── Edges ──
    for (const edge of simEdges) {
      const isSelectedEdge = selectedId && (edge.source.id === selectedId || edge.target.id === selectedId);
      const isHoveredEdge = hoveredNode && (edge.source.id === hoveredNode.id || edge.target.id === hoveredNode.id);

      // Curved edges — offset control point perpendicular to midpoint
      const mx = (edge.source.x + edge.target.x) / 2;
      const my = (edge.source.y + edge.target.y) / 2;
      const dx = edge.target.x - edge.source.x;
      const dy = edge.target.y - edge.source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const curvature = Math.min(dist * 0.08, 20);
      const cpx = mx + (-dy / dist) * curvature;
      const cpy = my + (dx / dist) * curvature;

      ctx.beginPath();
      ctx.moveTo(edge.source.x, edge.source.y);
      ctx.quadraticCurveTo(cpx, cpy, edge.target.x, edge.target.y);

      if (isSelectedEdge) {
        const color = getTypeColor(nodeMap[selectedId]?.type);
        ctx.strokeStyle = hexToRgba(color, 0.5);
        ctx.lineWidth = 2;
      } else if (isHoveredEdge) {
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
        ctx.lineWidth = 1.5;
      } else if (hasSelection || hasHover) {
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.03)';
        ctx.lineWidth = 0.5;
      } else {
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.07)';
        ctx.lineWidth = 0.8;
      }
      ctx.stroke();
    }

    // ── Nodes ──
    for (const node of simNodes) {
      const r = nodeRadius(node);
      const isSelected = selectedId === node.id;
      const isHovered = hoveredNode === node;
      const isConnectedToSelected = connectedToSelected.has(node.id);
      const isConnectedToHovered = connectedToHovered.has(node.id);
      const color = getTypeColor(node.type);

      // Determine opacity based on selection/hover state
      let nodeOpacity = 1;
      if ((hasSelection || hasHover) && !isSelected && !isHovered && !isConnectedToSelected && !isConnectedToHovered) {
        nodeOpacity = 0.15;
      }

      ctx.globalAlpha = nodeOpacity;

      // Outer glow
      if (isSelected || isHovered) {
        ctx.save();
        ctx.shadowColor = color;
        ctx.shadowBlur = isSelected ? 24 : 16;
        ctx.beginPath();
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
        ctx.fillStyle = 'transparent';
        ctx.fill();
        ctx.restore();
      }

      // Node fill — radial gradient for depth
      const grad = ctx.createRadialGradient(
        node.x - r * 0.3, node.y - r * 0.3, r * 0.1,
        node.x, node.y, r
      );
      if (isSelected) {
        grad.addColorStop(0, '#ffffff');
        grad.addColorStop(0.5, color);
        grad.addColorStop(1, darkenColor(color, 0.3));
      } else if (isHovered) {
        grad.addColorStop(0, lightenColor(color, 0.3));
        grad.addColorStop(1, color);
      } else {
        grad.addColorStop(0, lightenColor(color, 0.15));
        grad.addColorStop(1, darkenColor(color, 0.15));
      }

      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();

      // Selection ring
      if (isSelected) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + 3, 0, Math.PI * 2);
        ctx.strokeStyle = hexToRgba(color, 0.6);
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      ctx.globalAlpha = 1;
    }

    // ── Labels ──
    const effectiveZoom = zoom;
    if (effectiveZoom >= LABEL_ZOOM_THRESHOLD) {
      const labelAlpha = Math.min((effectiveZoom - LABEL_ZOOM_THRESHOLD) / 0.3, 1);

      for (const node of simNodes) {
        const r = nodeRadius(node);
        const isSelected = selectedId === node.id;
        const isHovered = hoveredNode === node;
        const isConnected = connectedToSelected.has(node.id) || connectedToHovered.has(node.id);

        // Only show labels for visible/relevant nodes when dimmed
        if ((hasSelection || hasHover) && !isSelected && !isHovered && !isConnected) continue;

        const label = truncateLabel(node.title || node.id);
        const fontSize = isSelected ? 12 : isHovered ? 11.5 : 11;
        ctx.font = `${isSelected || isHovered ? '600' : '400'} ${fontSize}px -apple-system, BlinkMacSystemFont, sans-serif`;
        const textWidth = ctx.measureText(label).width;
        const padding = 4;
        const pillW = textWidth + padding * 2;
        const pillH = fontSize + padding;
        const pillX = node.x - pillW / 2;
        const pillY = node.y + r + 6;

        // Label background pill
        const alpha = isSelected || isHovered ? 0.85 : 0.65 * labelAlpha;
        ctx.fillStyle = `rgba(15, 15, 26, ${alpha})`;
        ctx.beginPath();
        roundRect(ctx, pillX, pillY, pillW, pillH, 4);
        ctx.fill();

        // Label text
        ctx.fillStyle = isSelected ? '#ffffff' : isHovered ? '#e0e0e0' : `rgba(180, 180, 200, ${labelAlpha})`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(label, node.x, pillY + padding / 2);
      }
    }

    ctx.restore();

    // ── Hover tooltip ──
    if (hoveredNode && !isDragging) {
      drawTooltip(ctx, hoveredNode);
    }
  }

  function drawBackgroundDots(ctx, w, h) {
    const spacing = 30 * dpr;
    const dotSize = 1 * dpr;
    // Offset dots based on pan for parallax
    const ox = (panOffset.x * dpr * 0.5) % spacing;
    const oy = (panOffset.y * dpr * 0.5) % spacing;

    ctx.fillStyle = 'rgba(255, 255, 255, 0.025)';
    for (let x = ox; x < w; x += spacing) {
      for (let y = oy; y < h; y += spacing) {
        ctx.fillRect(x, y, dotSize, dotSize);
      }
    }
  }

  function drawTooltip(ctx, node) {
    if (!canvasEl) return;
    const screenX = node.x * zoom + panOffset.x;
    const screenY = node.y * zoom + panOffset.y;
    const r = nodeRadius(node) * zoom;

    const title = node.title || node.id;
    const type = node.type;
    const color = getTypeColor(type);

    ctx.save();
    ctx.scale(dpr, dpr);

    const titleFont = '600 13px -apple-system, BlinkMacSystemFont, sans-serif';
    const typeFont = '500 11px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.font = titleFont;
    const titleW = ctx.measureText(title).width;
    ctx.font = typeFont;
    const typeW = ctx.measureText(type).width;

    const padding = 10;
    const gap = 4;
    const tooltipW = Math.max(titleW, typeW + 14) + padding * 2;
    const tooltipH = 13 + 11 + gap + padding * 2;
    let tx = screenX - tooltipW / 2;
    let ty = screenY - r - tooltipH - 8;

    // Keep tooltip on screen
    tx = Math.max(8, Math.min(tx, canvasEl.width / dpr - tooltipW - 8));
    ty = Math.max(8, ty);

    // Shadow
    ctx.shadowColor = 'rgba(0, 0, 0, 0.5)';
    ctx.shadowBlur = 12;
    ctx.shadowOffsetY = 4;

    // Background
    ctx.fillStyle = '#1a2544';
    ctx.beginPath();
    roundRect(ctx, tx, ty, tooltipW, tooltipH, 8);
    ctx.fill();

    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;

    // Border
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    roundRect(ctx, tx, ty, tooltipW, tooltipH, 8);
    ctx.stroke();

    // Title
    ctx.font = titleFont;
    ctx.fillStyle = '#e0e0e0';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(title, tx + padding, ty + padding);

    // Type badge
    ctx.font = typeFont;
    const badgeY = ty + padding + 13 + gap;
    // Type dot
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(tx + padding + 4, badgeY + 5.5, 4, 0, Math.PI * 2);
    ctx.fill();
    // Type text
    ctx.fillStyle = '#8892a4';
    ctx.fillText(type, tx + padding + 14, badgeY);

    ctx.restore();
  }

  // ── Helpers ──

  function roundRect(ctx, x, y, w, h, r) {
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r);
    ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h);
    ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r);
    ctx.arcTo(x, y, x + r, y, r);
  }

  function parseHex(hex) {
    if (!hex || hex[0] !== '#') return [136, 136, 136];
    const h = hex.length === 4
      ? hex[1] + hex[1] + hex[2] + hex[2] + hex[3] + hex[3]
      : hex.slice(1);
    return [
      parseInt(h.slice(0, 2), 16) || 0,
      parseInt(h.slice(2, 4), 16) || 0,
      parseInt(h.slice(4, 6), 16) || 0,
    ];
  }

  function hexToRgba(hex, alpha) {
    const [r, g, b] = parseHex(hex);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function lightenColor(hex, amount) {
    let [r, g, b] = parseHex(hex);
    r = Math.min(255, Math.round(r + (255 - r) * amount));
    g = Math.min(255, Math.round(g + (255 - g) * amount));
    b = Math.min(255, Math.round(b + (255 - b) * amount));
    return `rgb(${r}, ${g}, ${b})`;
  }

  function darkenColor(hex, amount) {
    let [r, g, b] = parseHex(hex);
    r = Math.round(r * (1 - amount));
    g = Math.round(g * (1 - amount));
    b = Math.round(b * (1 - amount));
    return `rgb(${r}, ${g}, ${b})`;
  }

  function truncateLabel(text) {
    if (text.length <= LABEL_MAX_CHARS) return text;
    return text.slice(0, LABEL_MAX_CHARS - 1) + '\u2026';
  }

  // ── Interaction ──

  function screenToWorld(sx, sy) {
    return { x: (sx - panOffset.x) / zoom, y: (sy - panOffset.y) / zoom };
  }

  function findNodeAt(sx, sy) {
    const { x, y } = screenToWorld(sx, sy);
    // Search in reverse (top-rendered last) with generous hit area
    for (let i = simNodes.length - 1; i >= 0; i--) {
      const node = simNodes[i];
      const r = nodeRadius(node);
      const dx = node.x - x;
      const dy = node.y - y;
      if (dx * dx + dy * dy <= (r + 5) * (r + 5)) return node;
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
      settled = false; // Wake up physics when dragging
    } else {
      isPanning = true;
    }
    lastMouse = { x: e.clientX, y: e.clientY };
  }

  function handleMouseMove(e) {
    const rect = canvasEl.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;

    if (dragNode) {
      isDragging = true;
      const world = screenToWorld(sx, sy);
      dragNode.x = world.x;
      dragNode.y = world.y;
      dragNode.vx = 0;
      dragNode.vy = 0;
    } else if (isPanning) {
      const dx = e.clientX - lastMouse.x;
      const dy = e.clientY - lastMouse.y;
      panOffset.x += dx;
      panOffset.y += dy;
    } else {
      // Hover detection
      const hit = findNodeAt(sx, sy);
      if (hit !== hoveredNode) {
        hoveredNode = hit;
        canvasEl.style.cursor = hit ? 'pointer' : 'grab';
      }
    }
    lastMouse = { x: e.clientX, y: e.clientY };
  }

  async function handleMouseUp(e) {
    if (dragNode && !isDragging) {
      // Click — select node
      selectedId = dragNode.id;
      try {
        const data = await getNode(dragNode.id);
        selectedNodeDetail = data.node;
        selectedNeighbors = data.neighbors;
      } catch { selectedNodeDetail = null; }
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
    const delta = e.deltaY > 0 ? 0.93 : 1.07;
    zoom = Math.max(0.15, Math.min(4.0, zoom * delta));
    panOffset.x = mx - (mx - panOffset.x) * (zoom / oldZoom);
    panOffset.y = my - (my - panOffset.y) * (zoom / oldZoom);
  }

  function handleDoubleClick(e) {
    const rect = canvasEl.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const hit = findNodeAt(sx, sy);
    if (hit) {
      // Center and zoom into node
      const targetZoom = Math.min(zoom * 1.8, 3.0);
      const cx = canvasEl.width / (2 * dpr);
      const cy = canvasEl.height / (2 * dpr);
      zoom = targetZoom;
      panOffset.x = cx - hit.x * zoom;
      panOffset.y = cy - hit.y * zoom;
    }
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
    } catch { selectedNodeDetail = null; }
  }

  function resizeCanvas() {
    if (!canvasEl || !container) return;
    dpr = window.devicePixelRatio || 1;
    canvasEl.width = container.clientWidth * dpr;
    canvasEl.height = container.clientHeight * dpr;
    canvasEl.style.width = container.clientWidth + 'px';
    canvasEl.style.height = container.clientHeight + 'px';
  }

  async function loadGraph() {
    try {
      const data = await getGraph();
      allNodes = data.nodes;
      allEdges = data.edges;
      if (!allNodes.length) { isEmpty = true; return; }
      applyFilter();
      // Handle pending node selection after graph loads
      if (selectedNode && simNodes.length > 0) {
        handleNodeClick(selectedNode);
        const node = nodeMap[selectedNode];
        if (node && canvasEl) {
          const cx = canvasEl.width / (2 * dpr);
          const cy = canvasEl.height / (2 * dpr);
          panOffset.x = cx - node.x * zoom;
          panOffset.y = cy - node.y * zoom;
        }
      }
    } catch { isEmpty = true; }
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

  // Handle external node selection (e.g. from chat node chips)
  $effect(() => {
    if (selectedNode && simNodes.length > 0) {
      handleNodeClick(selectedNode);
      // Center on the selected node
      const node = nodeMap[selectedNode];
      if (node && canvasEl) {
        const cx = canvasEl.width / (2 * dpr);
        const cy = canvasEl.height / (2 * dpr);
        panOffset.x = cx - node.x * zoom;
        panOffset.y = cy - node.y * zoom;
      }
    }
  });
</script>

<div class="graph-view" bind:this={container}>
  {#if isEmpty}
    <div class="empty-state">
      <div class="empty-icon">
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
          <circle cx="16" cy="20" r="4" stroke="currentColor" stroke-width="1.5" opacity="0.4"/>
          <circle cx="32" cy="16" r="3" stroke="currentColor" stroke-width="1.5" opacity="0.3"/>
          <circle cx="28" cy="34" r="5" stroke="currentColor" stroke-width="1.5" opacity="0.5"/>
          <circle cx="12" cy="36" r="2.5" stroke="currentColor" stroke-width="1.5" opacity="0.25"/>
          <circle cx="38" cy="28" r="2" stroke="currentColor" stroke-width="1.5" opacity="0.2"/>
          <line x1="19.5" y1="21.5" x2="29" y2="15.5" stroke="currentColor" stroke-width="1" opacity="0.15"/>
          <line x1="17.5" y1="23" x2="25" y2="31" stroke="currentColor" stroke-width="1" opacity="0.15"/>
          <line x1="31" y1="18.5" x2="29" y2="30" stroke="currentColor" stroke-width="1" opacity="0.15"/>
          <line x1="14" y1="35" x2="24" y2="34" stroke="currentColor" stroke-width="1" opacity="0.15"/>
        </svg>
      </div>
      <h3>No nodes yet</h3>
      <p>Start a conversation and I'll map it out.</p>
    </div>
  {:else}
    <canvas
      bind:this={canvasEl}
      onmousedown={handleMouseDown}
      onmousemove={handleMouseMove}
      onmouseup={handleMouseUp}
      onmouseleave={() => { handleMouseUp(); hoveredNode = null; }}
      ondblclick={handleDoubleClick}
      onwheel={handleWheel}
    ></canvas>

    <div class="graph-controls">
      <button class="ctrl-btn" onclick={() => { zoom = Math.min(zoom * 1.3, 4); }} title="Zoom in">+</button>
      <button class="ctrl-btn" onclick={() => { zoom = Math.max(zoom * 0.7, 0.15); }} title="Zoom out">&minus;</button>
      <button class="ctrl-btn" onclick={() => { zoom = 1; panOffset = { x: 0, y: 0 }; }} title="Reset view">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <circle cx="7" cy="7" r="5.5" stroke="currentColor" stroke-width="1.2"/>
          <circle cx="7" cy="7" r="1.5" fill="currentColor"/>
        </svg>
      </button>
    </div>
  {/if}

  {#if selectedNodeDetail}
    <NodeDetail
      node={selectedNodeDetail}
      neighbors={selectedNeighbors}
      {schema}
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
    gap: var(--space-sm);
  }

  .empty-icon {
    color: var(--text-muted);
    margin-bottom: var(--space-sm);
  }

  .empty-state h3 {
    color: var(--text-primary);
    font-size: var(--text-lg);
    font-weight: 600;
  }

  .empty-state p {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  /* ── Controls ── */

  .graph-controls {
    position: absolute;
    bottom: var(--space-lg);
    right: var(--space-lg);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .ctrl-btn {
    width: 32px;
    height: 32px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--bg-surface);
    color: var(--text-secondary);
    font-size: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all var(--transition-fast);
    box-shadow: var(--shadow-sm);
  }

  .ctrl-btn:hover {
    background: var(--bg-surface-hover);
    color: var(--text-primary);
    border-color: var(--text-muted);
  }
</style>
