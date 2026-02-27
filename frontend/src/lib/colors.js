/**
 * Schema-driven type colours. Domain → base hue, types get shades.
 * Falls back to hardcoded map if schema not loaded yet.
 */

const TYPE_COLORS = {
  // Self (greens / pinks / oranges)
  goal: '#4ade80', fear: '#f87171', belief: '#fb923c', value: '#f472b6',
  habit: '#34d399', skill: '#a78bfa',
  // People (blues)
  person: '#60a5fa', organisation: '#3b82f6',
  // Knowledge (yellows / cyans)
  book: '#fbbf24', article: '#f59e0b', idea: '#eab308', note: '#a3a3a3',
  interest: '#22d3ee', movie: '#e9d5ff', quote: '#d4fc79', pill: '#ff6b6b',
  // Life (purples / grays)
  experience: '#e879f9', daily: '#94a3b8', memory: '#c084fc',
  // Planning (sky blues)
  task: '#38bdf8', project: '#0ea5e9', reminder: '#7dd3fc', event: '#06b6d4',
  // Places (rose)
  place: '#fb7185',
  // Finance (oranges)
  expense: '#f97316', subscription: '#ea580c', budget: '#c2410c',
};

const DOMAIN_COLORS = {
  Self: '#4ade80',
  People: '#60a5fa',
  Knowledge: '#fbbf24',
  Life: '#e879f9',
  Planning: '#38bdf8',
  Places: '#fb7185',
  Finance: '#f97316',
};

export function getTypeColor(type) {
  return TYPE_COLORS[type] || '#888888';
}

export function getDomainColor(domain) {
  return DOMAIN_COLORS[domain] || '#888888';
}

export { TYPE_COLORS, DOMAIN_COLORS };
