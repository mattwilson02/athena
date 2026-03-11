<script>
  import { T } from '@threlte/core';
  import * as THREE from 'three';

  let {
    positions,        // Float32Array of node positions
    edgeIndices,      // Uint16Array of [sourceIdx, targetIdx, ...]
    selectedId = null,
    hoveredId = null,
    nodeIndexById = {},
    simNodes = [],
  } = $props();

  // Build line geometry from node positions + edge index pairs
  const edgePositions = $derived.by(() => {
    if (!positions || !edgeIndices) return new Float32Array(0);
    const arr = new Float32Array(edgeIndices.length * 3);
    for (let i = 0; i < edgeIndices.length; i++) {
      const ni = edgeIndices[i];
      arr[i * 3] = positions[ni * 3];
      arr[i * 3 + 1] = positions[ni * 3 + 1];
      arr[i * 3 + 2] = positions[ni * 3 + 2];
    }
    return arr;
  });

  // Highlight edges connected to selected/hovered node
  const edgeColors = $derived.by(() => {
    const count = edgeIndices.length / 2;
    const colors = new Float32Array(edgeIndices.length * 3);
    const activeIdx = selectedId ? nodeIndexById[selectedId] : (hoveredId ? nodeIndexById[hoveredId] : null);

    for (let i = 0; i < count; i++) {
      const si = edgeIndices[i * 2];
      const ti = edgeIndices[i * 2 + 1];
      const isActive = activeIdx !== null && activeIdx !== undefined && (si === activeIdx || ti === activeIdx);

      // Much subtler: 4% default, 35% when connected to active node, 1% when something else is active
      const brightness = isActive ? 0.35 : (activeIdx !== null && activeIdx !== undefined ? 0.01 : 0.04);
      colors[i * 6] = brightness;
      colors[i * 6 + 1] = brightness;
      colors[i * 6 + 2] = brightness;
      colors[i * 6 + 3] = brightness;
      colors[i * 6 + 4] = brightness;
      colors[i * 6 + 5] = brightness;
    }
    return colors;
  });

  const geometry = new THREE.BufferGeometry();

  $effect(() => {
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(edgePositions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(edgeColors, 3));
    geometry.attributes.position.needsUpdate = true;
    geometry.attributes.color.needsUpdate = true;
  });
</script>

<T.LineSegments {geometry}>
  <T.LineBasicMaterial
    vertexColors
    transparent
    opacity={0.9}
    depthWrite={false}
  />
</T.LineSegments>
