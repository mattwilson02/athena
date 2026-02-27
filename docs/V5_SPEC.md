# Athena V5 — Containerisation + Hardening + Telegram

## Context

Athena currently runs bare on localhost with no containerisation or auth. Two problems:

1. **Security** — unauthenticated API, no process isolation, secrets in env vars
2. **Access** — can only use Athena at the desk, no mobile access

Telegram gives mobile access, but first Athena must be properly containerised and hardened — you can't expose an unauthenticated localhost API to the internet.

Security patterns are borrowed from a reference containerised knowledge-management project with proven containment architecture: non-root containers, capability dropping, bearer token auth, audit logging, secrets as files (not env vars), and path traversal prevention.

---

## Current State

| Component | Status |
|-----------|--------|
| Flask backend | Bare on localhost:5001, no auth, no containerisation |
| Claude API | Direct via Anthropic SDK (`anthropic==0.43.0`), per-token billing |
| n8n | Not set up |
| Telegram | No bot handling |
| Docker | Minimal docker-compose exists, no security hardening |

### Claude API Touchpoints (4 total)

| Endpoint | File | Call Style |
|----------|------|-----------|
| `POST /api/chat/stream` | `mentor_agent.py:525` | `client.messages.stream()` (SSE) |
| `POST /api/chat` | `mentor_agent.py:602` | `client.messages.create()` (sync) |
| `GET /api/insights` | `insights_routes.py:40` | Fresh `anthropic.Anthropic()` per call |
| `POST /api/graph/suggest-links` | `graph_routes.py:181` | Fresh `anthropic.Anthropic()` per call |

All 4 were consolidated into a shared client factory in Phase 2.

---

## Security Model

This is a personal knowledge graph containing goals, fears, finances, relationships — the entire inner map of someone's life. The security posture must reflect that. Every layer assumes the one above it is compromised.

### Principle: Explicit Access, Not Default Access

Nothing gets access to anything unless we specifically grant it. The Docker container cannot see the host filesystem. The Telegram channel cannot see graph updates. The n8n webhook cannot write to the vault. Every boundary is enforced by code, not by trust.

### Container Filesystem Isolation

The Athena container runs with **no access to the host filesystem** except these explicit mounts:

| Mount | Access | Why |
|-------|--------|-----|
| `./vault` | read/write | The knowledge graph — markdown files |
| `chroma_db` named volume | read/write | Vector embeddings (regenerable, no sensitive data) |
| `chat_sessions` named volume | read/write | Chat history JSON files |
| `./deployment/config` | **read-only** | Auth tokens, routing config |
| `./deployment/secrets` | **read-only** | API key file (chmod 600) |
| `./SOUL.md` | **read-only** | Personality definition |
| `logs` named volume | read/write | Audit trail |

**That's it.** The container cannot access your home directory, SSH keys, browser data, other repos, or anything else on disk. Named volumes (`chroma_db`, `chat_sessions`, `logs`) are managed by Docker and isolated from the host filesystem tree.

### Container Process Isolation

| Control | Setting | What it prevents |
|---------|---------|-----------------|
| Drop all capabilities | `cap_drop: ALL` | No system administration, no raw sockets, no kernel module loading |
| No privilege escalation | `no-new-privileges: true` | Cannot gain root via setuid binaries, exploits, or misconfiguration |
| Non-root user | `USER athena` (uid 1000) | Even if code is exploited, attacker has no root access |
| Memory limit | `mem_limit: 2g` | Cannot exhaust host memory |
| CPU limit | `cpus: 2` | Cannot starve other processes |

### Network Isolation

Three Docker networks enforce strict traffic separation:

```
proxy-net (bridge)              ← nginx publishes port here
    └── athena-proxy

athena-net (internal: true)     ← all three services communicate here
    ├── athena-proxy            ← bridges proxy-net ↔ athena-net
    ├── athena-frontend         ← fully isolated, no internet
    └── athena                  ← also on gateway-net for Claude API

gateway-net (bridge)            ← backend reaches internet/host
    └── athena                  ← Claude API (direct)
```

| Container | Networks | Can reach internet? | Published ports |
|-----------|----------|-------------------|-----------------|
| `athena-proxy` (nginx) | proxy-net, athena-net | No | `127.0.0.1:8080:8080` |
| `athena-frontend` | athena-net | **No** | None |
| `athena` (backend) | athena-net, gateway-net | Yes (Claude API only) | None |

**How `internal: true` works:** Docker Desktop for Mac blocks port publishing on internal networks. To solve this, only the nginx reverse proxy bridges between the host-facing `proxy-net` (standard bridge) and the isolated `athena-net` (internal). The backend and frontend never publish ports — they are only reachable through the proxy on the internal network.

**Why the backend needs `gateway-net`:** The backend calls the Claude API directly at `api.anthropic.com`. This requires outbound network access. The `gateway-net` bridge network provides this without exposing the backend to the host's published ports.

**Frontend is fully isolated:** The frontend container serves static files and has no outbound network access. Even if the nginx process inside were compromised, the attacker can only reach other containers on `athena-net` — not the internet, not the host, nothing else.

**Same-origin through proxy:** Since the browser accesses everything through `http://localhost:8080` (the proxy), both the frontend HTML and API responses come from the same origin. CORS is irrelevant in production — no cross-origin requests occur.

**Layer 2: Host firewall** — restricts which host ports the backend can reach via `gateway-net`.

`host.docker.internal` resolves to the host machine. The backend on `gateway-net` can reach **any port** listening on the host — not just the Claude API.

**macOS Docker Desktop reality:** Docker Desktop runs containers inside a lightweight Linux VM. Container traffic to `host.docker.internal` traverses the VM boundary via `com.docker.backend`, not through the macOS `lo0` loopback interface. This means **macOS pf rules on `lo0` may not intercept container traffic**. The pf rules below are still worth applying (they protect against non-Docker local traffic), but they are not a reliable sole defence.

**Primary mitigation: bind services to loopback only.** Ensure host services only listen on `127.0.0.1`, not `0.0.0.0`. If a service isn't listening on the Docker VM's gateway IP, the container can't reach it.

**Secondary mitigation: pf rules (defence-in-depth, may not intercept Docker traffic):**

```bash
# Add to /etc/pf.conf or a separate anchor file
# Block all container traffic to host loopback by default
block drop quick on lo0 proto tcp from 172.16.0.0/12 to 127.0.0.1
# No pass rules needed — Claude API is reached via the internet, not via the host
```

**After all layers**, the containers cannot:

- **Frontend:** Reach the internet, scan the LAN, or publish ports (blocked by `internal: true`)
- **Proxy:** Reach the internet or the host (only on `proxy-net` + `athena-net`, neither provides outbound)
- **Backend:** Publish ports to the host (no port mapping), but CAN reach internet via `gateway-net` (required for Claude API)

**Accepted risk:** The backend can reach the internet via `gateway-net` — this is necessary for Claude API calls. The frontend and proxy are fully isolated. The backend's outbound access is limited to what the application code does (Claude API calls only). If the backend were compromised, `gateway-net` provides an exfiltration path — but the backend runs our own code as a non-root user with all capabilities dropped.

### Authentication & Permissions

Every write operation requires a bearer token. Each token maps to a named user with scoped permissions:

| User | Allowed Endpoints | Use Case |
|------|-------------------|----------|
| `web_ui` | `*` (all) | Full access from the Svelte frontend |
| `n8n` | `chat`, `vault` | Webhook automation (Phase 3) |
| `telegram` | `chat` only | Text-only chat — **no vault writes, no graph reads, no insights** |

Read endpoints (`GET /api/graph`, `GET /api/schema`, etc.) are open by default for local dev convenience. All write endpoints (`POST`, `PATCH`, `DELETE`) always require auth.

**Optional read auth:** Config flag `auth.protect_reads: true` extends auth to GET endpoints. Not needed while Athena is only reachable from localhost, but should be enabled if the container is ever reachable from other containers (e.g. n8n in Phase 3) or LAN devices. This prevents unauthenticated graph scraping.

### Path Traversal Prevention

Two hardening fixes applied to existing code:

1. **Vault paths** — Replace the weak `".." in folder` string check with `safe_resolve()` using `Path.resolve()` + `.relative_to()`. This handles edge cases like encoded characters, symlinks, and normalisation bypasses.

2. **Session IDs** — Validate `session_id` format before constructing file paths. Regex accepts: UUIDs (`[a-f0-9-]+`), Telegram sessions (`tg-<digits>` — Telegram chat IDs are decimal integers), and legacy WhatsApp sessions (`wa-<hex>`).

### Secrets Management

Secrets are stored as **files** in `deployment/secrets/` (chmod 600), never as environment variables.

Why files, not env vars:
- `docker inspect` dumps all env vars in plaintext
- Process listings (`/proc/*/environ`) expose env vars
- Logging frameworks often capture env vars in crash reports
- Files with restrictive permissions are only readable by the container process

The `deployment/secrets/` directory is gitignored. Secrets include the Anthropic API key, bearer auth tokens, and proxy auth config (Phase 1-2). Telegram bot token is stored in n8n credentials (Phase 3).

### Audit Trail

Every write operation (POST, PATCH, DELETE) is logged to `/logs/audit.log` as JSON Lines:

```json
{"timestamp": "2026-02-27T14:30:00Z", "method": "POST", "path": "/api/vault/write", "user": "web_ui", "status": 200, "ip": "172.17.0.1"}
{"timestamp": "2026-02-27T14:31:00Z", "method": "POST", "path": "/api/chat", "user": "telegram", "status": 200, "ip": "172.17.0.1"}
{"timestamp": "2026-02-27T14:32:00Z", "method": "POST", "path": "/api/vault/write", "user": "anonymous", "status": 401, "ip": "172.17.0.1"}
```

If something goes wrong — unauthorised access, unexpected writes, suspicious patterns — the audit log tells you who, what, when, and from where.

### CORS Lockdown

The current backend uses `flask-cors` with default settings, which sets `Access-Control-Allow-Origin: *`. This allows **any website** to make JavaScript requests to the Athena API from your browser — a DNS rebinding attack could scrape your entire knowledge graph silently.

**Fix:** Lock the CORS origin to the Svelte frontend only.

```python
# server.py — replace CORS(app) with:
CORS(app, origins=["http://localhost:5173", "http://localhost:8080"])
```

Two origins allowed:
- `http://localhost:5173` — Vite dev server (bare dev mode)
- `http://localhost:8080` — nginx reverse proxy (production Docker mode)

In production mode, CORS is effectively irrelevant — the browser accesses everything through the proxy at `localhost:8080`, so all requests are same-origin. CORS only matters in dev mode when the frontend (5173) talks directly to the backend (5001).

### Telegram Channel Restrictions (Phase 3)

The Telegram integration is the most exposed surface. It faces the public internet (via Cloudflare tunnel) and accepts messages from an external platform. Extra precautions:

| Layer | Control |
|-------|---------|
| Cloudflare tunnel | TLS termination, DDoS protection, no direct IP exposure |
| Non-guessable webhook path | Include a secret token segment in the webhook URL (e.g. `/webhook/<secret>`) — reduces random scanning |
| Cloudflare rate limit | Global rate limit on the webhook path at Cloudflare level — stops volumetric abuse before it hits n8n |
| Telegram bot token | Webhook only receives updates from Telegram's servers (verified by secret token in URL path) |
| **Chat ID allowlist** | **Single-user only** — your Telegram chat ID is the only one processed. All others silently dropped. Empty by default (fully locked down until you add your ID). |
| Rate limiting | 10 messages per minute per sender (prevents spam/abuse) |
| Field validation | Required fields checked before any processing |
| Bearer token | n8n → Athena calls use a scoped `telegram` token (chat-only, no direct vault access) |
| Text-only responses | Telegram never sees raw `<graph_updates>` XML, node metadata, or edge data |
| Vault writes via confirmation only | Graph updates stored as pending proposals. User must explicitly reply "yes"/"confirm" to write. No arbitrary vault writes — only updates Athena proposed. |
| Session isolation | Each chat ID gets its own session (prefixed with `tg-`) |
| Session cap | Max 100 messages per Telegram session before auto-reset |

**n8n admin UI isolation:** The Cloudflare tunnel must only expose the webhook endpoint path — **never the n8n admin UI**. If the n8n UI is accessible, an attacker can create/modify workflows, call arbitrary HTTP endpoints, and exfiltrate data. The n8n UI should only be reachable from `localhost:5678` directly. n8n must have built-in authentication enabled with a strong password.

Even if someone compromises the Telegram webhook entirely, the worst they can do is send chat messages to Athena and confirm graph updates that Athena herself proposed. They cannot craft arbitrary vault writes, read the graph, access insights, or reach any other endpoint. The `telegram` bearer token has `chat` permission only — vault writes happen internally through the chat service.

### Code Audit Findings (Pre-Existing)

A security audit of the current codebase found **no critical vulnerabilities**:

- No `eval()`, `exec()`, `subprocess`, `pickle`, or dynamic code execution anywhere
- `yaml.safe_load()` used everywhere (not `yaml.load()`)
- Node IDs sanitised via regex: only `[a-z0-9-]` characters survive
- ChromaDB uses `PersistentClient` (local-only, no network)
- All dependencies pinned to specific versions
- No outbound network calls except to Anthropic's API

The two moderate findings (session ID validation + folder path check) are addressed in Phase 1.

---

## Phase Order

```
Phase 1: Containerise + Harden ──→ Phase 2: Client Refactor + Key Hardening ──→ Phase 3: n8n + Telegram
```

Each phase is independently deployable and testable. Phase 1 is the foundation.

---

## Phase 1: Containerise Athena

### Goal

Docker container with hardened security posture: non-root user, capabilities dropped, bearer token auth on all write endpoints, audit logging, secrets stored as files.

### Threat Model

| Threat | Mitigation |
|--------|-----------|
| Container escape / privilege escalation | `cap_drop: ALL`, `no-new-privileges`, non-root user (uid 1000) |
| Unauthorised API access | Bearer token auth on all write endpoints |
| Path traversal (vault escape) | `_safe_resolve()` — resolve + relative_to check |
| Session ID injection | UUID format validation on `session_id` |
| Secrets in logs / Docker inspect | Secrets as files (chmod 600), never env vars |
| Untracked write operations | Audit logging (JSON Lines) on all mutations |
| Resource exhaustion | Memory limit (2GB), CPU limit (2 cores) |

### New Files

#### `backend/Dockerfile`

```dockerfile
FROM python:3.12-slim

RUN useradd -m -u 1000 -s /bin/bash athena

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R athena:athena /app

USER athena
EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/health')" || exit 1

CMD ["python", "server.py"]
```

#### `frontend/Dockerfile`

Multi-stage build: Node builds Svelte → nginx:alpine serves static files.

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

#### `frontend/nginx.conf`

SPA config — serves `index.html` for all routes.

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

#### `deployment/nginx/nginx.conf`

Reverse proxy — routes `/api` to backend, everything else to frontend. SSE streaming support.

```nginx
server {
    listen 80;
    location /api/ {
        proxy_pass http://athena:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
    location / {
        proxy_pass http://athena-frontend:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

#### `docker-compose.yml`

Three containers, three networks, `internal: true` restored. See Network Isolation section for topology.

```yaml
services:
  proxy:
    image: nginx:alpine
    container_name: athena-proxy
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:80"              # Only published port — loopback only
    volumes:
      - ./deployment/nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on: [athena, frontend]
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    cap_add: [NET_BIND_SERVICE]
    mem_limit: 256m
    networks: [proxy-net, athena-net]

  frontend:
    build: ./frontend
    container_name: athena-frontend
    restart: unless-stopped
    networks:
      - athena-net                        # Internal only — no internet, no published ports
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    cap_add: [NET_BIND_SERVICE]
    mem_limit: 256m

  athena:
    build: ./backend
    container_name: athena
    restart: unless-stopped
    # NO ports published — only reachable via proxy on athena-net
    environment:
      - VAULT_PATH=/vault
      - CONFIG_PATH=/config/config.yaml
      - FLASK_ENV=production
    volumes:
      - ./vault:/vault:rw
      - athena-chroma:/app/chroma_db:rw
      - athena-sessions:/app/chat_sessions:rw
      - ./deployment/config:/config:ro
      - ./deployment/secrets:/secrets:ro
      - ./SOUL.md:/app/SOUL.md:ro
      - athena-logs:/logs:rw
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    mem_limit: 2g
    cpus: 2
    extra_hosts: ["host.docker.internal:host-gateway"]
    networks:
      - athena-net                        # Internal — communicates with proxy + frontend
      - gateway-net                       # External — reaches Claude API + host
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s

networks:
  proxy-net:
    driver: bridge                        # Host-facing — proxy publishes port here
  athena-net:
    internal: true                        # Isolated — no internet, no port publishing
  gateway-net:
    driver: bridge                        # Backend reaches Claude API + host.docker.internal

volumes:
  athena-chroma:
  athena-sessions:
  athena-logs:
```

#### `deployment/config/config.yaml`

```yaml
# Athena Configuration
# NOTE: No secrets in this file. Tokens live in deployment/secrets/auth_tokens.yaml

auth:
  protect_reads: false           # Set true to require auth on GET endpoints (recommended for Phase 3+)

# User permissions — which endpoint groups each user can access
users:
  web_ui:
    endpoints: ["*"]             # Full access (chat, vault, graph, insights)
  n8n:
    endpoints: ["chat", "vault"] # Chat + write (no graph browsing)
  telegram:
    endpoints: ["chat"]          # Chat only — no vault writes, no graph reads

# Claude API
claude:
  model: "claude-sonnet-4-20250514"

# Audit
audit:
  log_path: "/logs/audit.log"
  log_reads: false               # Only log write operations by default
```

#### `deployment/secrets/` (gitignored, chmod 600)

All secrets live here — separated from config so config can be shared, logged, or diffed safely.

```
deployment/secrets/
├── anthropic_api_key.txt        # Anthropic API key
└── auth_tokens.yaml             # Bearer tokens → user mapping
```

`auth_tokens.yaml`:
```yaml
# Generate tokens with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
tokens:
  "REPLACE_WITH_GENERATED_TOKEN_1": "web_ui"
  "REPLACE_WITH_GENERATED_TOKEN_2": "n8n"        # Phase 3
  "REPLACE_WITH_GENERATED_TOKEN_3": "telegram"   # Phase 3
```

#### `backend/middleware/__init__.py` (empty)

#### `backend/middleware/auth.py`

Bearer token authentication, registered as Flask `before_request` hook.

```python
"""Bearer token authentication + permission scoping middleware."""

from flask import current_app, jsonify, request

# Endpoints that require authentication (all write operations)
PROTECTED_PREFIXES = [
    ("POST", "/api/chat"),
    ("POST", "/api/vault"),
    ("DELETE", "/api/chat"),
    ("PATCH", "/api/chat"),
]

# Map URL path prefixes to permission groups
# Used to enforce per-user endpoint scoping from config.yaml
PATH_TO_GROUP = {
    "/api/chat": "chat",
    "/api/vault": "vault",
    "/api/graph": "graph",
    "/api/insights": "insights",
    "/api/node": "graph",
    "/api/search": "graph",
    "/api/schema": "graph",
    "/api/activity": "graph",
}

def _get_endpoint_group(path: str) -> str | None:
    """Map a request path to its permission group."""
    for prefix, group in PATH_TO_GROUP.items():
        if path.startswith(prefix):
            return group
    return None

def authenticate():
    """Validate bearer token. Returns username or None."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1]
    auth_tokens = current_app.config.get("auth_tokens", {})
    return auth_tokens.get(token)

def _check_permission(username: str) -> bool:
    """Check if authenticated user is allowed to access this endpoint."""
    config = current_app.config.get("athena_config", {})
    user_config = config.get("users", {}).get(username, {})
    allowed = user_config.get("endpoints", [])

    if "*" in allowed:
        return True

    group = _get_endpoint_group(request.path)
    return group is not None and group in allowed

def require_auth():
    """Flask before_request hook — reject unauthenticated or unauthorised requests."""
    # Skip auth for read endpoints and health checks
    is_protected = any(
        request.method == method and request.path.startswith(prefix)
        for method, prefix in PROTECTED_PREFIXES
    )
    if not is_protected:
        return None  # Allow through

    username = authenticate()
    if username is None:
        return jsonify({"error": "Unauthorized"}), 401

    # Check endpoint permission scoping
    if not _check_permission(username):
        return jsonify({"error": "Forbidden"}), 403

    # Store authenticated user for audit logging
    request.auth_user = username
    return None
```

#### `backend/middleware/audit.py`

JSON Lines audit log for all write operations.

```python
"""Audit logging middleware."""

import json
import os
from datetime import datetime, timezone
from flask import request

def log_audit(response):
    """Flask after_request hook — log write operations."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return response

    log_path = os.getenv("AUDIT_LOG", "/logs/audit.log")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "user": getattr(request, "auth_user", "anonymous"),
        "status": response.status_code,
        "ip": request.remote_addr,
    }

    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Don't crash the request if audit logging fails

    return response
```

#### `backend/middleware/security.py`

Path traversal prevention.

```python
"""Path traversal prevention."""

from pathlib import Path

def safe_resolve(base: Path, user_path: str) -> Path:
    """Resolve a user-provided path safely within a base directory.

    Returns the resolved path. Raises ValueError if the path escapes the base.
    """
    resolved = (base / user_path).resolve()
    resolved.relative_to(base.resolve())  # Raises ValueError if outside base
    return resolved
```

### Modified Files

| File | Change |
|------|--------|
| `server.py` | Load config from YAML file, load auth tokens from `secrets/auth_tokens.yaml`, load API key from `secrets/anthropic_api_key.txt`, register `require_auth` as `before_request`, register `log_audit` as `after_request`, add `GET /api/health` endpoint. Store tokens on `app.config["auth_tokens"]`. Keep `load_dotenv()` as dev-mode fallback. **CORS origins:** `http://localhost:5173` (dev) + `http://localhost:8080` (production proxy). |
| `vault_service.py` | Replace weak `".." in folder` check (line 59) with `safe_resolve()`. Apply to `write()`, `update()`, `repair()`. |
| `chat_store.py` | Validate `session_id` in `_session_path()` — reject anything that isn't UUID `[a-f0-9-]`, `tg-<digits>` (Telegram chat IDs are decimal), or `wa-<hex>` (Phase 3). |
| `frontend/src/lib/api.js` | Add `Authorization: Bearer ${token}` header to all `fetch` calls. Token loaded from localStorage or build-time env var. |
| `.gitignore` | Add `deployment/secrets/`, `logs/` |

### Verification Checklist

- [ ] `docker compose up --build` — all three containers start healthy
- [ ] `curl localhost:8080/api/health` → `{"status":"ok"}` (through proxy)
- [ ] `curl localhost:8080` → Svelte frontend HTML (through proxy)
- [ ] `curl localhost:5001` → connection refused (backend port not published)
- [ ] `curl localhost:8080/api/graph` returns graph data (no auth required)
- [ ] `POST localhost:8080/api/vault/write` without token returns 401
- [ ] Same request with valid bearer token returns 200
- [ ] `docker exec athena whoami` returns `athena` (non-root)
- [ ] Vault files persist across `docker compose down && docker compose up`
- [ ] `/logs/audit.log` records write operations with timestamp, user, path
- [ ] `docker exec athena-frontend wget -q -O- http://example.com` → fails (internal: true)
- [ ] All 118 tests pass locally (dev mode unaffected)
- [ ] Path traversal attempt (`../../../etc/passwd`) blocked

### Rollback

Run Flask bare: `cd backend && python3 server.py`. Vault files are bind-mounted (not in named volume), so zero data risk. Config/secrets are additive — existing `.env` still works.

---

## Phase 2: Client Refactor + API Key Hardening

### Goal

Consolidate all Claude API access through a shared client factory. Harden API key handling — remove the key from `os.environ` and `config` after boot, sanitize error messages, and restrict `gateway-net` egress.

### Why Not OpenClaw

OpenClaw was originally planned as a subscription billing proxy. After investigation, it was found to be architecturally incompatible with Athena:

- **Dynamic system prompts** — Athena builds fresh context per message (semantic search + 2-hop graph traversal + session boosting). OpenClaw caches system prompts per-session — stale context after the first message.
- **Streaming** — OpenClaw CLI is synchronous only. SSE streaming (core UX feature) would be lost.
- **Agent ownership** — OpenClaw owns the agent loop. Athena's custom retrieval pipeline, prompt engineering, and graph-aware context would need to be abandoned or awkwardly shoehorned in.
- **Cost math** — API tokens at personal usage (500-1000 msgs/month) cost ~$5-35/month. Claude Max subscription ($100-200/month) is more expensive, not less.

Athena's purpose-built agent outperforms a generic runtime for knowledge graph use cases. Direct Anthropic SDK access preserves streaming, per-message context, and immediate access to new SDK features.

### Current Problem

4 separate Claude API touchpoints, each creating their own client:

| Location | How client is created |
|----------|----------------------|
| `mentor_agent.py:356` | `anthropic.Anthropic()` in `__init__` |
| `insights_routes.py:40` | `anthropic.Anthropic()` per request |
| `graph_routes.py:181` | `anthropic.Anthropic()` per request |
| `chat_service.py` | No client — catches errors from mentor |

All rely on `ANTHROPIC_API_KEY` env var. The key sits in 3 places in memory (`os.environ`, `config["_secrets"]`, `app.config`). Raw exception messages are sent to clients, risking accidental key leakage.

### New Files

#### `backend/claude_client.py`

Shared client factory — single source of truth for all Claude API access.

```python
"""Shared Claude client factory — single source of truth for all Claude API access."""
from __future__ import annotations

import logging
import anthropic

logger = logging.getLogger(__name__)

def create_claude_client(api_key: str) -> anthropic.Anthropic | None:
    """Create an Anthropic client with explicit key. Returns None if no key."""
    if not api_key:
        logger.error("No Claude API key — Claude disabled")
        return None

    logger.info("Claude client ready (direct API)")
    return anthropic.Anthropic(api_key=api_key)
```

### Modified Files

| File | Change |
|------|--------|
| `server.py` | Import `create_claude_client`. Create shared client at boot with explicit key. Pass to MentorAgent. Store on `app.config["claude_client"]`. Clear key from `os.environ` and `config["_secrets"]` after client creation. |
| `mentor_agent.py` | Accept injected `client` parameter in `__init__` instead of creating `anthropic.Anthropic()`. Remove self-initialisation. |
| `insights_routes.py` | Replace standalone `client = anthropic.Anthropic()` with `current_app.config["claude_client"]`. Add null check → 503. |
| `graph_routes.py` | Replace standalone `client = anthropic.Anthropic()` with `current_app.config["claude_client"]`. Add null check → 503. |
| `chat_service.py` | Add `ConnectionError` handling for network issues. |
| `server.py` (error handlers) | Replace `str(e)` in client-facing error responses with generic messages. Keep detailed logging server-side. |
| `chat_routes.py` (error handlers) | Same — generic error messages to client, detailed logs server-side. |

### API Key Hardening

After the shared client is created at boot, the raw API key string is removed from all accessible locations:

```python
# server.py — after creating claude_client
del os.environ["ANTHROPIC_API_KEY"]              # Remove from /proc/1/environ
config.get("_secrets", {}).pop("anthropic_api_key", None)  # Remove from config dict
```

The key now lives only inside the `anthropic.Anthropic` client object (which holds it as an internal attribute). An attacker with code execution can no longer read it from `os.environ` or `config`.

Error handlers sanitized — raw `str(e)` replaced with generic messages in all client-facing responses. Detailed errors logged server-side only.

### Verification Checklist

- [ ] Chat message works (streaming + sync)
- [ ] Insights (`GET /api/insights`) returns analysis
- [ ] Suggest-links (`POST /api/graph/suggest-links`) works
- [ ] `docker exec athena env | grep ANTHROP` returns nothing (key cleared from env)
- [ ] `docker exec athena python -c "import os; print(os.environ.get('ANTHROPIC_API_KEY'))"` returns `None`
- [ ] Trigger a server error → client sees "Internal server error", not a raw exception
- [ ] All 118 tests still pass locally (dev mode unaffected)

### Rollback

Revert the 6 modified files. The key loading path (`_load_secrets`) is unchanged — only the post-boot cleanup and client injection are new.

---

## Phase 3: n8n + Telegram

### Goal

Text Athena from Telegram, get text responses back — including the ability to accept graph updates via confirm/decline keywords. n8n handles webhook routing and validation. Cloudflare tunnel provides secure external exposure.

### Architecture

```
Phone (Telegram)
    │
    ▼
Telegram Bot API ─── webhook POST ───▶ Cloudflare Tunnel (TLS + DDoS protection)
                                            │
                                            ▼
                                       n8n (port 5678)
                                       ├─ Validate: chat ID on allowlist
                                       ├─ Validate: required fields
                                       ├─ Rate limit: 10 msg/min per sender
                                       │
                                       ▼
                                  Athena API (bearer token auth)
                                  POST /api/chat/simple
                                       │
                                       ├─ Normal message → chat response (text + pending updates)
                                       ├─ "yes"/"confirm" → write pending update to vault
                                       └─ "no"/"skip"    → dismiss pending update
                                       │
                                       ▼
                                  Format response (text only, ≤4096 chars)
                                       │
                                       ▼
                                  Send reply via Telegram Bot API (sendMessage)
```

### Telegram Graph Update Flow

When Athena proposes a graph update in a Telegram session, the update is stored as `pending_updates` in the session (not sent to the user as raw `<graph_updates>` XML). Instead, a human-readable summary is appended to the text response:

```
User: "I started reading Atomic Habits"

Athena: "Smart pick. James Clear's framework maps directly onto your
habit-tracking nodes. The 1% improvement model connects to your
cut-to-83kg goal — small daily wins compound.

→ Add book node 'Atomic Habits'? Reply yes/no."
```

The user confirms or declines with a keyword:

| Keyword | Action |
|---------|--------|
| `yes`, `confirm`, `accept`, `y` | Write pending update to vault, rebuild graph |
| `no`, `decline`, `skip`, `n` | Dismiss pending update |
| Anything else | Normal chat message (pending update stays) |

**Key design decisions:**
- Updates are proposed **one at a time** — if Athena suggests 3 updates, they queue and the user confirms each sequentially
- Pending updates expire after 10 messages (auto-dismissed if the user moves on)
- The `telegram` user keeps `chat`-only permission — vault writes happen internally through the chat service, never through a direct vault endpoint
- The confirmation prompt is generated server-side (appended to Athena's response), not by Athena herself — so it can't be prompt-injected away

### Threat Model (Telegram-specific)

| Threat | Mitigation |
|--------|-----------|
| Unauthorised webhook calls | Secret token in webhook URL path — only Telegram knows the path. Additionally, n8n validates the update structure. |
| DoS / spam | Rate limiting (10 msg/min per sender), Cloudflare protection |
| Unauthorised senders | **Single-user allowlist** — only your Telegram chat ID is processed, all others silently dropped. Starts empty (locked down by default). |
| Malformed payloads | Required field validation before processing |
| Cross-user session leakage | Sessions keyed by `tg-{chat_id}` |
| Graph update exposure to Telegram | Text-only responses — no raw `<graph_updates>` XML, no node metadata, no edge data |
| Vault pollution via Telegram | Confirmations only apply to updates Athena proposed in the current session. No arbitrary vault writes — `telegram` user has `chat` permission only, vault writes are internal. |
| Stolen telegram bearer token | Attacker can only chat + confirm existing proposals. Cannot craft vault writes, read graph, or access insights. Proposals are AI-generated (attacker can't control what gets proposed). |
| Unbounded session growth | Max message cap per Telegram session (auto-reset) |
| n8n compromise | n8n has no vault mount, no API keys. Bearer token grants `chat` only. Even full n8n takeover limits attacker to sending chat messages as the telegram user. |
| n8n → Athena spoofing | Bearer token auth on internal calls |
| n8n admin UI exposure | Cloudflare tunnel restricted to webhook path only — n8n UI never exposed to internet. Admin UI on `localhost:5678` only. |

### Prerequisites (from Phase 1+2)

These are already in place:

- `chat_store.py` session ID regex accepts `tg-` prefixed IDs
- `config.yaml` has `n8n` and `telegram` user permissions defined
- `auth_tokens.yaml` has placeholder entries (tokens must be generated)
- Auth middleware enforces endpoint scoping per user
- `athena-net` exists as `internal: true` Docker network

### New Files

#### `deployment/n8n/docker-compose.yml`

```yaml
services:
  n8n:
    image: n8nio/n8n:latest
    container_name: athena-n8n
    restart: unless-stopped
    ports:
      - "127.0.0.1:5678:5678"           # Loopback only — NOT reachable from LAN
    environment:
      - N8N_HOST=localhost
      - N8N_PORT=5678
      - WEBHOOK_URL=https://YOUR_TUNNEL.trycloudflare.com
      - N8N_DIAGNOSTICS_ENABLED=false
      - N8N_PERSONALIZATION_ENABLED=false
      - N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}
      - NODE_ENV=production
    volumes:
      - n8n-data:/home/node/.n8n
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE
    mem_limit: 2g
    cpus: 2
    networks:
      - n8n-net
      - athena-net
    healthcheck:
      test: ["CMD-SHELL", "wget --spider -q http://localhost:5678/healthz || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3

networks:
  n8n-net:
    driver: bridge
    # n8n needs outbound to Telegram Bot API (api.telegram.org) via n8n-net.
    # Cannot use internal:true because it must reach Telegram's API to send replies.
    # n8n's damage is limited even without firewall restrictions — it has no vault mount,
    # no API keys, and Athena's bearer token only grants `chat` access.
  athena-net:
    external: true
    name: athena_athena-net
    # Joins the internal network created by the main docker-compose.
    # Gives n8n direct access to athena:5001 without publishing ports.
    # Requires the main Athena stack to be running first.

volumes:
  n8n-data:
```

#### n8n Workflow (configured in UI, exported as JSON)

Nodes:
1. **Telegram Trigger** — n8n built-in Telegram trigger node, receives updates via webhook
2. **Chat ID Allowlist** — Code node: check sender chat ID against allowlist, silently drop if not whitelisted (allowlist configured in n8n credentials, starts empty)
3. **Rate Limit** — Code node: track per-sender message count, reject if >10/min
4. **Route to Athena** — HTTP Request: `POST http://athena:5001/api/chat/simple` with bearer token (via `athena-net`). Body: `{"session_id": "tg-{chat_id}", "message": "{text}"}`
5. **Format Response** — Code node: strip to plain text, truncate to 4096 chars (Telegram limit)
6. **Send Reply** — Telegram node: `sendMessage` back to the chat

#### New Secrets

Generate n8n + telegram bearer tokens and add to `auth_tokens.yaml`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"  # n8n token
python3 -c "import secrets; print(secrets.token_urlsafe(32))"  # telegram token
```

Generate n8n encryption key (used to encrypt stored credentials):

```bash
openssl rand -hex 32 > deployment/n8n/.env  # N8N_ENCRYPTION_KEY=<value>
```

Full secrets directory after Phase 3:

```
deployment/secrets/
├── anthropic_api_key.txt        # Phase 1
├── auth_tokens.yaml             # Phase 1+3 — bearer tokens for web_ui, n8n, telegram
├── proxy_auth.conf              # Phase 1 — nginx include, injects web_ui bearer token
deployment/n8n/.env              # N8N_ENCRYPTION_KEY (loaded by docker compose)
```

The Telegram bot token is stored as a credential inside n8n (encrypted with `N8N_ENCRYPTION_KEY`), not as a file in `deployment/secrets/`. This is simpler than WhatsApp — no app secret, no verify token, no access token.

### Modified Files

| File | Change |
|------|--------|
| `chat_store.py` | Add `get_or_create_session(session_id)` for stable Telegram sessions (keyed by `tg-{chat_id}`). Add `set_pending_updates(session_id, updates)` and `pop_pending_update(session_id)` for the confirm/decline queue. Add max message count check — return error if session exceeds cap. |
| `chat_routes.py` | Add `POST /api/chat/simple` — synchronous text-only response. Detects confirm/decline keywords and delegates to `chat_service.confirm_pending()` or `chat_service.dismiss_pending()`. For Telegram and other text-only clients. |
| `chat_service.py` | Add `send_simple_message(session_id, message)` — calls mentor, stores graph_updates as pending in session, appends confirmation prompt to text response, truncates to max length. Add `confirm_pending(session_id)` — pops next pending update, writes to vault via vault_service, returns confirmation text. Add `dismiss_pending(session_id)` — pops and dismisses. |
| `config.yaml` | Add `telegram:` section: `max_response_length: 4096`, `session_prefix: "tg-"`, `max_session_messages: 100`. **Set `auth.protect_reads: true`** — now that n8n exists as a separate container, read endpoints must require auth to prevent unauthenticated graph scraping. |
| `auth_tokens.yaml` | Add generated tokens for `n8n` and `telegram` users. |

### Infrastructure Setup

**Auth tokens:**
1. Generate n8n + telegram bearer tokens (see "New Secrets" above)
2. Add to `deployment/secrets/auth_tokens.yaml`
3. Rebuild Athena container: `docker compose up -d --build athena`

**n8n:**
4. Generate n8n encryption key and write to `deployment/n8n/.env`
5. Start n8n: `cd deployment/n8n && docker compose up -d`
6. Access `localhost:5678`, create admin account on first launch
7. Build the Telegram workflow (see n8n Workflow section)

**Telegram bot:**
8. Open Telegram, message `@BotFather`, send `/newbot`
9. Choose a name and username — BotFather gives you a bot token
10. Add bot token as a Telegram credential in n8n
11. n8n's Telegram trigger node auto-registers the webhook with Telegram

**Cloudflare tunnel:**
12. Install cloudflared: `brew install cloudflare/cloudflare/cloudflared`
13. Start quick tunnel: `cloudflared tunnel --url http://localhost:5678`
14. Copy the generated URL → set as `WEBHOOK_URL` in n8n docker-compose
15. Restart n8n to pick up the new webhook URL

#### Cloudflare Tunnel Config

Using a quick tunnel (`cloudflared tunnel --url http://localhost:5678`) for simplicity. The quick tunnel generates a random `*.trycloudflare.com` URL.

**Security note:** The quick tunnel exposes all of n8n's ports. Ensure n8n has built-in authentication enabled (set up on first launch). The Telegram webhook path includes a secret token that prevents unauthorized access. For additional lockdown, use a named tunnel with ingress path restrictions:

```yaml
# Optional: named tunnel with path restriction
tunnel: athena-webhook
ingress:
  - hostname: athena-webhook.YOUR_DOMAIN.com
    path: /webhook/*
    service: http://localhost:5678
  - service: http_status:404
```

### Verification Checklist

**Backend changes:**
- [x] `POST /api/chat/simple` returns text-only response (no graph_updates)
- [x] `POST /api/chat/simple` without bearer token → 401
- [x] `POST /api/chat/simple` with telegram token → 200 (chat permission)
- [x] `GET /api/graph` without token → 401 (protect_reads enabled)
- [x] Telegram session hits message cap → error response
- [x] Web UI completely unaffected by Telegram changes

**n8n + tunnel:**
- [x] n8n starts: `cd deployment/n8n && docker compose up -d`
- [ ] n8n health: `curl localhost:5678/healthz` returns 200
- [ ] n8n can reach Athena: workflow HTTP node → `http://athena:5001/api/health` returns 200
- [x] Cloudflare tunnel running, webhook URL accessible externally

**End-to-end:**
- [ ] Send "Hello" from Telegram → get Athena text response
- [ ] Message from non-allowlisted chat ID → silently dropped
- [ ] 15 rapid messages → rate limited after 10
- [ ] Restart n8n → webhook still works
- [ ] Telegram response contains no `<graph_updates>` blocks

### Rollback

n8n is in a separate docker-compose — stop it without touching Athena. Revoke bot token via @BotFather if needed. Delete `tg-` prefixed session files if needed. Zero impact to web UI.

---

## File Summary

| Phase | New Files | Modified Files |
|-------|-----------|---------------|
| 1 | `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`, `deployment/nginx/nginx.conf`, `docker-compose.yml`, `deployment/config/config.yaml`, `deployment/secrets/`, `backend/middleware/{__init__,auth,audit,security}.py` | `server.py`, `vault_service.py`, `chat_store.py`, `api.js`, `.gitignore` |
| 2 | `backend/claude_client.py` | `server.py`, `mentor_agent.py`, `insights_routes.py`, `graph_routes.py`, `chat_service.py`, `chat_routes.py` |
| 3 | `deployment/n8n/docker-compose.yml`, n8n workflow (JSON export), new secrets | `chat_store.py`, `chat_service.py`, `chat_routes.py`, `server.py`, `config.yaml`, `auth_tokens.yaml` |

---

## Open Questions

- ~~WhatsApp Business: virtual number~~ — Switched to Telegram (no Meta developer account needed, simpler setup)

---

## Principles

Borrowed from the reference project's containment philosophy:

1. **Containment by architecture, not trust** — security enforced by code structure (capabilities, path resolution, token validation), not by hoping things behave
2. **Secrets as files, never env vars** — prevents exposure in Docker inspect, logs, process listings
3. **Audit everything that mutates** — if it writes to the vault or changes a session, it gets logged
4. **Each phase is independently reversible** — if Telegram breaks, stop n8n. Each phase can be rolled back without affecting the others.
5. **Text-only for external channels** — Telegram gets Athena's words but never graph update proposals, node data, or internal metadata
