/**
 * Domain → radial layer mapping for the "Solar System" layout.
 * Self at center, transactional stuff at the periphery.
 */

// Domain → base radius — spread wide for clear separation
const DOMAIN_RADIUS = {
  Self: 20,        // Layer 0 — Core
  People: 55,      // Layer 1 — Inner orbit
  Life: 95,        // Layer 2 — Middle orbit
  Planning: 115,   // Layer 2 — Middle orbit
  Knowledge: 165,  // Layer 3 — Outer orbit
  Places: 180,     // Layer 3 — Outer orbit
  Finance: 230,    // Layer 4 — Periphery
};

// Domain importance — used for node sizing (core = bigger)
const DOMAIN_IMPORTANCE = {
  Self: 1.0,
  People: 0.8,
  Life: 0.65,
  Planning: 0.6,
  Knowledge: 0.45,
  Places: 0.4,
  Finance: 0.3,
};

// Type → domain lookup (built from schema at runtime, this is fallback)
const TYPE_TO_DOMAIN = {
  goal: 'Self', fear: 'Self', belief: 'Self', value: 'Self',
  habit: 'Self', skill: 'Self',
  person: 'People', organisation: 'People',
  book: 'Knowledge', article: 'Knowledge', idea: 'Knowledge', note: 'Knowledge',
  interest: 'Knowledge', movie: 'Knowledge', quote: 'Knowledge', pill: 'Knowledge',
  experience: 'Life', daily: 'Life', memory: 'Life',
  task: 'Planning', project: 'Planning', reminder: 'Planning', event: 'Planning',
  place: 'Places',
  expense: 'Finance', subscription: 'Finance', budget: 'Finance',
};

/**
 * Build TYPE_TO_DOMAIN from schema if available.
 */
export function buildTypeToDomain(schema) {
  if (!schema?.domains) return TYPE_TO_DOMAIN;
  const map = {};
  for (const [domain, info] of Object.entries(schema.domains)) {
    for (const type of info.types || []) {
      map[type] = domain;
    }
  }
  return map;
}

/**
 * Calculate target radius for a node based on domain + connection count.
 * More connections = closer to center within the domain's shell.
 */
export function targetRadius(node, connectionCount, typeToDomain) {
  const domain = (typeToDomain || TYPE_TO_DOMAIN)[node.type] || 'Knowledge';
  const base = DOMAIN_RADIUS[domain] || 165;
  // Pull more-connected nodes inward (max 30% closer)
  const pull = Math.min(connectionCount * 2.0, base * 0.3);
  return Math.max(8, base - pull);
}

/**
 * Node visual size — based on domain importance + connection count.
 * Core nodes (Self) are biggest, periphery (Finance) smallest.
 * More connections = larger within the range.
 */
export function nodeSize(node, connectionCount, typeToDomain) {
  const domain = (typeToDomain || TYPE_TO_DOMAIN)[node.type] || 'Knowledge';
  const importance = DOMAIN_IMPORTANCE[domain] || 0.4;
  // Base: 0.6 (periphery) to 1.5 (core). Connections add up to +1.0.
  const base = 0.6 + importance * 0.9;
  const connBonus = Math.min(connectionCount * 0.15, 1.0);
  return base + connBonus;
}

export { DOMAIN_RADIUS, DOMAIN_IMPORTANCE, TYPE_TO_DOMAIN };
