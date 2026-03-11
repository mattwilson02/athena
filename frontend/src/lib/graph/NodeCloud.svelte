<script>
  import { T } from '@threlte/core';
  import * as THREE from 'three';
  import { getTypeColor } from '../colors.js';

  let {
    simNodes = [],
    positions,
    sizes,
    selectedId = null,
    hoveredId = null,
    nodeIndexById = {},
    edgeIndices = null,   // For neighbor highlighting
  } = $props();

  let meshRef = $state(null);
  const dummy = new THREE.Object3D();
  const colorObj = new THREE.Color();

  // Build neighbor set for active node
  function getNeighborIndices(activeIdx) {
    if (activeIdx === undefined || activeIdx === null || !edgeIndices) return new Set();
    const neighbors = new Set();
    const count = edgeIndices.length / 2;
    for (let i = 0; i < count; i++) {
      const si = edgeIndices[i * 2];
      const ti = edgeIndices[i * 2 + 1];
      if (si === activeIdx) neighbors.add(ti);
      if (ti === activeIdx) neighbors.add(si);
    }
    return neighbors;
  }

  // Update instanced mesh transforms + colors
  $effect(() => {
    if (!meshRef || !simNodes.length) return;

    const activeIdx = selectedId !== null ? nodeIndexById[selectedId]
                    : hoveredId !== null ? nodeIndexById[hoveredId]
                    : undefined;
    const hasActive = activeIdx !== undefined;
    const neighbors = hasActive ? getNeighborIndices(activeIdx) : new Set();

    for (let i = 0; i < simNodes.length; i++) {
      dummy.position.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
      const isActive = i === activeIdx;
      const isNeighbor = neighbors.has(i);
      const isHovered = simNodes[i].id === hoveredId;
      const scale = isActive ? sizes[i] * 2.0 : isHovered ? sizes[i] * 1.5 : sizes[i];
      dummy.scale.setScalar(scale);
      dummy.updateMatrix();
      meshRef.setMatrixAt(i, dummy.matrix);
    }
    meshRef.instanceMatrix.needsUpdate = true;

    for (let i = 0; i < simNodes.length; i++) {
      colorObj.set(getTypeColor(simNodes[i].type));
      const isActive = i === activeIdx;
      const isNeighbor = neighbors.has(i);

      if (hasActive) {
        if (isActive) {
          // Selected: boost brightness
          colorObj.multiplyScalar(1.3);
        } else if (isNeighbor) {
          // Neighbors: 70% brightness
          colorObj.multiplyScalar(0.7);
        } else {
          // Everything else: dim
          colorObj.multiplyScalar(0.1);
        }
      }

      meshRef.setColorAt(i, colorObj);
    }
    if (meshRef.instanceColor) meshRef.instanceColor.needsUpdate = true;
  });
</script>

{#if simNodes.length > 0}
  <T.InstancedMesh
    bind:ref={meshRef}
    args={[undefined, undefined, simNodes.length]}
    frustumCulled={false}
  >
    <T.SphereGeometry args={[1, 16, 12]} />
    <T.MeshBasicMaterial toneMapped={false} />
  </T.InstancedMesh>
{/if}
