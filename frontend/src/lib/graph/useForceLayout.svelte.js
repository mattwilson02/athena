/**
 * Svelte 5 rune-based 3D force simulation using d3-force-3d.
 * Runs the simulation, exposes reactive node positions.
 */
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCollide,
  forceRadial,
} from 'd3-force-3d';
import { targetRadius, nodeSize, buildTypeToDomain } from './layout.js';

/**
 * Create and run a 3D force layout.
 *
 * @param {Array} nodes - graph nodes [{id, type, title, ...}]
 * @param {Array} edges - graph edges [{source, target, type}]
 * @param {Object} schema - parsed schema for domain mapping
 */
export function createForceLayout(nodes, edges, schema) {
  const typeToDomain = buildTypeToDomain(schema);

  // Count connections per node
  const connectionCount = {};
  for (const n of nodes) connectionCount[n.id] = 0;
  for (const e of edges) {
    connectionCount[e.source] = (connectionCount[e.source] || 0) + 1;
    connectionCount[e.target] = (connectionCount[e.target] || 0) + 1;
  }

  // Build simulation nodes with initial random positions
  const simNodes = nodes.map((n) => ({
    id: n.id,
    type: n.type,
    title: n.title,
    connections: connectionCount[n.id] || 0,
    x: (Math.random() - 0.5) * 80,
    y: (Math.random() - 0.5) * 80,
    z: (Math.random() - 0.5) * 80,
  }));

  const nodeById = {};
  for (const n of simNodes) nodeById[n.id] = n;

  const simLinks = edges
    .filter((e) => nodeById[e.source] && nodeById[e.target])
    .map((e) => ({ source: e.source, target: e.target }));

  // Radial target per node
  const radialTargets = {};
  for (const n of simNodes) {
    radialTargets[n.id] = targetRadius(n, n.connections, typeToDomain);
  }

  const simulation = forceSimulation(simNodes, 3)
    .force(
      'radial',
      forceRadial((d) => radialTargets[d.id]).strength(0.35),
    )
    .force(
      'link',
      forceLink(simLinks)
        .id((d) => d.id)
        .distance(20)
        .strength(0.12),
    )
    .force('charge', forceManyBody().strength(-80).distanceMax(200))
    .force('collide', forceCollide().radius((d) => nodeSize(d, d.connections, typeToDomain) + 1.5))
    .alphaDecay(0.012)
    .velocityDecay(0.3)
    .stop();

  // Run simulation ticks synchronously
  const TICKS = 350;
  for (let i = 0; i < TICKS; i++) simulation.tick();

  // Build output arrays
  const positions = new Float32Array(simNodes.length * 3);
  const sizes = new Float32Array(simNodes.length);
  for (let i = 0; i < simNodes.length; i++) {
    positions[i * 3] = simNodes[i].x;
    positions[i * 3 + 1] = simNodes[i].y;
    positions[i * 3 + 2] = simNodes[i].z;
    sizes[i] = nodeSize(simNodes[i], simNodes[i].connections, typeToDomain);
  }

  // Build edge index pairs
  const edgeIndices = [];
  const nodeIndexById = {};
  for (let i = 0; i < simNodes.length; i++) nodeIndexById[simNodes[i].id] = i;
  for (const link of simLinks) {
    const si = nodeIndexById[typeof link.source === 'object' ? link.source.id : link.source];
    const ti = nodeIndexById[typeof link.target === 'object' ? link.target.id : link.target];
    if (si !== undefined && ti !== undefined) {
      edgeIndices.push(si, ti);
    }
  }

  return {
    simNodes,
    positions,
    sizes,
    edgeIndices: new Uint16Array(edgeIndices),
    connectionCount,
    nodeIndexById,
  };
}
