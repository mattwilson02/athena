<script>
  import { T } from '@threlte/core';
  import * as THREE from 'three';

  const COUNT = 4000;
  const SPREAD = 800;

  const positions = new Float32Array(COUNT * 3);
  const colors = new Float32Array(COUNT * 3);
  for (let i = 0; i < COUNT; i++) {
    positions[i * 3] = (Math.random() - 0.5) * SPREAD;
    positions[i * 3 + 1] = (Math.random() - 0.5) * SPREAD;
    positions[i * 3 + 2] = (Math.random() - 0.5) * SPREAD;

    // Vary star brightness and slight color tint
    const brightness = Math.random() * 0.6 + 0.2;
    const tint = Math.random();
    if (tint < 0.1) {
      // Slight blue tint
      colors[i * 3] = brightness * 0.7;
      colors[i * 3 + 1] = brightness * 0.8;
      colors[i * 3 + 2] = brightness;
    } else if (tint < 0.15) {
      // Slight warm tint
      colors[i * 3] = brightness;
      colors[i * 3 + 1] = brightness * 0.85;
      colors[i * 3 + 2] = brightness * 0.7;
    } else {
      colors[i * 3] = brightness;
      colors[i * 3 + 1] = brightness;
      colors[i * 3 + 2] = brightness;
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
</script>

<T.Points {geometry}>
  <T.PointsMaterial
    vertexColors
    size={0.5}
    transparent
    opacity={0.7}
    sizeAttenuation
    depthWrite={false}
  />
</T.Points>
