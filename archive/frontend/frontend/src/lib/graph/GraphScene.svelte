<script>
  import { T, Canvas } from '@threlte/core';
  import { OrbitControls } from '@threlte/extras';
  import StarField from './StarField.svelte';
  import NodeCloud from './NodeCloud.svelte';
  import EdgeLines from './EdgeLines.svelte';
  import NodeLabel from './NodeLabel.svelte';
  import CameraExporter from './CameraExporter.svelte';
  import BloomEffect from './BloomEffect.svelte';

  let {
    simNodes = [],
    positions,
    sizes,
    edgeIndices,
    nodeIndexById = {},
    selectedId = null,
    hoveredId = null,
    onCameraReady = () => {},
  } = $props();
</script>

<Canvas>
  <T.PerspectiveCamera
    makeDefault
    position={[0, 120, 320]}
    fov={60}
    near={0.1}
    far={2000}
  >
    <OrbitControls
      enableDamping
      dampingFactor={0.12}
      autoRotate
      autoRotateSpeed={0.15}
      minDistance={15}
      maxDistance={600}
    />
  </T.PerspectiveCamera>

  <CameraExporter onCamera={onCameraReady} />
  <BloomEffect />

  <T.AmbientLight intensity={0.6} />
  <T.Color args={['#060610']} attach="background" />

  <StarField />

  <EdgeLines
    {positions}
    {edgeIndices}
    {selectedId}
    {hoveredId}
    {nodeIndexById}
    {simNodes}
  />

  <NodeCloud
    {simNodes}
    {positions}
    {sizes}
    {selectedId}
    {hoveredId}
    {nodeIndexById}
    {edgeIndices}
  />

  <NodeLabel
    {simNodes}
    {positions}
    {sizes}
    {hoveredId}
    {selectedId}
    {nodeIndexById}
  />
</Canvas>
