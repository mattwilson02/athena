const BASE = '/api';

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export function sendMessage(message) {
  return fetchJSON(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });
}

export function resetChat() {
  return fetchJSON(`${BASE}/chat/reset`, { method: 'POST' });
}

export function getGraph() {
  return fetchJSON(`${BASE}/graph`);
}

export function getGraphStats() {
  return fetchJSON(`${BASE}/graph/stats`);
}

export function getNode(nodeId) {
  return fetchJSON(`${BASE}/node/${encodeURIComponent(nodeId)}`);
}

export function getNodesByType(type) {
  return fetchJSON(`${BASE}/nodes?type=${encodeURIComponent(type)}`);
}

export function searchNodes(query) {
  return fetchJSON(`${BASE}/search?q=${encodeURIComponent(query)}`);
}

export function writeNode(nodeData) {
  return fetchJSON(`${BASE}/vault/write`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(nodeData),
  });
}

export function rebuildVault() {
  return fetchJSON(`${BASE}/vault/rebuild`, { method: 'POST' });
}

export function getInsights() {
  return fetchJSON(`${BASE}/insights`);
}
