const BASE = '/api';

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

// --- Sessions ---

export function listSessions() {
  return fetchJSON(`${BASE}/chat/sessions`);
}

export function createSession() {
  return fetchJSON(`${BASE}/chat/sessions`, { method: 'POST' });
}

export function getSession(sessionId) {
  return fetchJSON(`${BASE}/chat/sessions/${encodeURIComponent(sessionId)}`);
}

export function deleteSession(sessionId) {
  return fetchJSON(`${BASE}/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
}

// --- Chat ---

export function sendMessage(sessionId, message) {
  return fetchJSON(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

// --- Graph ---

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

// --- Vault ---

export function writeNode(nodeData) {
  return fetchJSON(`${BASE}/vault/write`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(nodeData),
  });
}

export function updateNode(nodeId, changes) {
  return fetchJSON(`${BASE}/vault/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: nodeId, changes }),
  });
}

export function rebuildVault() {
  return fetchJSON(`${BASE}/vault/rebuild`, { method: 'POST' });
}

// --- Schema ---

export function getSchema() {
  return fetchJSON(`${BASE}/schema`);
}

// --- Insights ---

export function getInsights() {
  return fetchJSON(`${BASE}/insights`);
}
