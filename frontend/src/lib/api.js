const BASE = '/api';

function getAuthHeaders() {
  const token = localStorage.getItem('athena_token');
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

async function fetchJSON(url, options = {}) {
  options.headers = { ...getAuthHeaders(), ...options.headers };
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

export function renameSession(sessionId, title) {
  return fetchJSON(`${BASE}/chat/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
}

export function dismissUpdate(sessionId, updateKey) {
  return fetchJSON(`${BASE}/chat/sessions/${encodeURIComponent(sessionId)}/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ update_key: updateKey }),
  });
}

// --- Chat ---

export function sendMessage(sessionId, message) {
  return fetchJSON(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

export function streamMessage(sessionId, message, { onText, onDone, onError }) {
  const controller = new AbortController();
  fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Parse SSE lines
        const lines = buffer.split('\n');
        buffer = lines.pop(); // keep incomplete line in buffer
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === 'text') onText(event.content);
            else if (event.type === 'done') onDone(event);
            else if (event.type === 'error') onError(new Error(event.error));
          } catch { /* skip malformed lines */ }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError(err);
    });
  return controller;
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

// --- Activity ---

export function getActivity(limit = 50) {
  return fetchJSON(`${BASE}/activity?limit=${limit}`);
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

export function suggestLinks(nodeId = null) {
  return fetchJSON(`${BASE}/graph/suggest-links`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(nodeId ? { node_id: nodeId } : {}),
  });
}

// --- Schema ---

export function getSchema() {
  return fetchJSON(`${BASE}/schema`);
}

// --- Insights ---

export function getInsights() {
  return fetchJSON(`${BASE}/insights`);
}

// --- Accountability ---

export function getAccountability() {
  return fetchJSON(`${BASE}/accountability`);
}

// --- Relationships ---

export function getRelationships() {
  return fetchJSON(`${BASE}/relationships`);
}

// --- Briefing ---

export function getBriefing(date = null) {
  const params = date ? `?date=${date}` : '';
  return fetchJSON(`${BASE}/briefing${params}`);
}

// --- Debug ---

export function getRetrievalDiagnostics(query) {
  return fetchJSON(`${BASE}/debug/retrieval?q=${encodeURIComponent(query)}`);
}
