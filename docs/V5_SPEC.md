# Athena V5 — OpenClaw Migration + WhatsApp + Containerisation

## Context

Athena currently runs bare on localhost with direct Claude API token billing. Two problems:

1. **Cost** — paying per API token when a Claude subscription is already being paid for
2. **Access** — can only use Athena at the desk, no mobile access

OpenClaw lets us route Claude calls through a subscription instead of API tokens. WhatsApp gives mobile access. Both require Athena to be properly containerised and hardened first — you can't expose an unauthenticated localhost API to the internet.

Security patterns are borrowed from a reference containerised knowledge-management project with proven containment architecture: non-root containers, capability dropping, bearer token auth, audit logging, secrets as files (not env vars), and path traversal prevention.

---

## Current State

| Component | Status |
|-----------|--------|
| Flask backend | Bare on localhost:5001, no auth, no containerisation |
| Claude API | Direct via Anthropic SDK (`anthropic==0.43.0`), per-token billing |
| OpenClaw | Not installed |
| n8n | Not set up |
| WhatsApp | No webhook handling |
| Docker | Minimal docker-compose exists, no security hardening |

### Claude API Touchpoints (4 total)

| Endpoint | File | Call Style |
|----------|------|-----------|
| `POST /api/chat/stream` | `mentor_agent.py:525` | `client.messages.stream()` (SSE) |
| `POST /api/chat` | `mentor_agent.py:602` | `client.messages.create()` (sync) |
| `GET /api/insights` | `insights_routes.py:40` | Fresh `anthropic.Anthropic()` per call |
| `POST /api/graph/suggest-links` | `graph_routes.py:181` | Fresh `anthropic.Anthropic()` per call |

All 4 need to be routed through a shared client that can point at OpenClaw.

---

## Security Model

This is a personal knowledge graph containing goals, fears, finances, relationships — the entire inner map of someone's life. The security posture must reflect that. Every layer assumes the one above it is compromised.

### Principle: Explicit Access, Not Default Access

Nothing gets access to anything unless we specifically grant it. The Docker container cannot see the host filesystem. The WhatsApp channel cannot see graph updates. The n8n webhook cannot write to the vault. Every boundary is enforced by code, not by trust.

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
    └── athena                  ← Claude API + OpenClaw (Phase 2)
```

| Container | Networks | Can reach internet? | Published ports |
|-----------|----------|-------------------|-----------------|
| `athena-proxy` (nginx) | proxy-net, athena-net | No | `127.0.0.1:8080:8080` |
| `athena-frontend` | athena-net | **No** | None |
| `athena` (backend) | athena-net, gateway-net | Yes (Claude API only) | None |

**How `internal: true` works:** Docker Desktop for Mac blocks port publishing on internal networks. To solve this, only the nginx reverse proxy bridges between the host-facing `proxy-net` (standard bridge) and the isolated `athena-net` (internal). The backend and frontend never publish ports — they are only reachable through the proxy on the internal network.

**Why the backend needs `gateway-net`:** The backend calls the Claude API directly (Phase 1) or via OpenClaw on `host.docker.internal:18789` (Phase 2). Both require outbound network access. The `gateway-net` bridge network provides this without exposing the backend to the host's published ports.

**Frontend is fully isolated:** The frontend container serves static files and has no outbound network access. Even if the nginx process inside were compromised, the attacker can only reach other containers on `athena-net` — not the internet, not the host, nothing else.

**Same-origin through proxy:** Since the browser accesses everything through `http://localhost:8080` (the proxy), both the frontend HTML and API responses come from the same origin. CORS is irrelevant in production — no cross-origin requests occur.

**Layer 2: Host firewall** — restricts which host ports the backend can reach via `gateway-net`.

`host.docker.internal` resolves to the host machine. The backend on `gateway-net` can reach **any port** listening on the host — not just OpenClaw on 18789.

**macOS Docker Desktop reality:** Docker Desktop runs containers inside a lightweight Linux VM. Container traffic to `host.docker.internal` traverses the VM boundary via `com.docker.backend`, not through the macOS `lo0` loopback interface. This means **macOS pf rules on `lo0` may not intercept container traffic**. The pf rules below are still worth applying (they protect against non-Docker local traffic), but they are not a reliable sole defence.

**Primary mitigation: bind services to loopback only.** Ensure host services only listen on `127.0.0.1`, not `0.0.0.0`. If a service isn't listening on the Docker VM's gateway IP, the container can't reach it.

**Secondary mitigation: pf rules (defence-in-depth, may not intercept Docker traffic):**

```bash
# Add to /etc/pf.conf or a separate anchor file
block drop quick on lo0 proto tcp from 172.16.0.0/12 to 127.0.0.1
pass quick on lo0 proto tcp from 172.16.0.0/12 to 127.0.0.1 port 18789  # OpenClaw gateway
```

**After all layers**, the containers cannot:

- **Frontend:** Reach the internet, scan the LAN, or publish ports (blocked by `internal: true`)
- **Proxy:** Reach the internet or the host (only on `proxy-net` + `athena-net`, neither provides outbound)
- **Backend:** Publish ports to the host (no port mapping), but CAN reach internet via `gateway-net` (required for Claude API)

**Accepted risk:** The backend can reach the internet via `gateway-net` — this is necessary for Claude API calls. The frontend and proxy are fully isolated. The backend's outbound access is limited to what the application code does (Claude API calls only). If the backend were compromised, `gateway-net` provides an exfiltration path — but the backend runs our own code as a non-root user with all capabilities dropped.

### OpenClaw Trust Model

**OpenClaw is the weakest link in this architecture.** Everything else is either our own code or well-established infrastructure (Docker, Cloudflare, n8n). OpenClaw is a third-party npm package running natively on the host with full host-level access and no sandboxing.

**What OpenClaw is:** A transparent HTTP proxy that routes Claude API calls through a Claude subscription instead of per-token billing. It runs on the host (not in the container) as a Node.js process.

**What it can see:** Every prompt sent to Claude — your goals, fears, finances, relationships, the entire knowledge graph context. This is a **new trust boundary**, not the same as before. Previously, data went directly from your machine to Anthropic over HTTPS. Now it passes through an intermediary process that has full host access.

**What a compromised OpenClaw could do:**
- Exfiltrate prompt content silently (it handles all API traffic)
- Read any file on the host (it runs as your user, unsandboxed)
- Modify prompts or responses in transit
- Phone home (it has full network access)

**Mitigations:**

| Control | Why |
|---------|-----|
| Pin to exact version (`openclaw@X.Y.Z`, never `@latest`) | Prevents malicious updates from auto-installing |
| Audit source before install | Verify the npm package matches the published source |
| Check for telemetry | Confirm it doesn't phone home (`lsof -i` after install) |
| Dedicated `openclaw` system user | Runs under a restricted user with its own home dir — cannot read your files, SSH keys, or other repos even if compromised |
| Host outbound firewall | Restrict OpenClaw's outbound to Anthropic endpoints only (e.g. LuLu, Little Snitch, or pf rules) — blocks silent exfiltration to other destinations |
| Loopback binding only (`127.0.0.1:18789`) | Not reachable from the network |
| Fallback to direct API | If OpenClaw is suspect, set `claude.base_url: null` and revert instantly |

**Host setup for dedicated user:**
```bash
# Create restricted openclaw user (no shell login, own home dir)
sudo dscl . -create /Users/openclaw
sudo dscl . -create /Users/openclaw UserShell /usr/bin/false
sudo dscl . -create /Users/openclaw NFSHomeDirectory /var/lib/openclaw
sudo mkdir -p /var/lib/openclaw && sudo chown openclaw /var/lib/openclaw

# Install and run OpenClaw as that user
sudo -u openclaw npm install -g openclaw@X.Y.Z
sudo -u openclaw openclaw gateway start
```

**Accepted risk:** We're trusting OpenClaw with prompt content in exchange for subscription-based billing. This is a conscious trade-off. The mitigations reduce the attack surface but don't eliminate it — if the OpenClaw project is compromised, our prompts are exposed. Running under a dedicated user limits the blast radius to OpenClaw's own data and Anthropic credentials. The direct API fallback means we can cut OpenClaw out instantly if trust is lost.

### Authentication & Permissions

Every write operation requires a bearer token. Each token maps to a named user with scoped permissions:

| User | Allowed Endpoints | Use Case |
|------|-------------------|----------|
| `web_ui` | `*` (all) | Full access from the Svelte frontend |
| `n8n` | `chat`, `vault` | Webhook automation (Phase 3) |
| `whatsapp` | `chat` only | Text-only chat — **no vault writes, no graph reads, no insights** |

Read endpoints (`GET /api/graph`, `GET /api/schema`, etc.) are open by default for local dev convenience. All write endpoints (`POST`, `PATCH`, `DELETE`) always require auth.

**Optional read auth:** Config flag `auth.protect_reads: true` extends auth to GET endpoints. Not needed while Athena is only reachable from localhost, but should be enabled if the container is ever reachable from other containers (e.g. n8n in Phase 3) or LAN devices. This prevents unauthenticated graph scraping.

### Path Traversal Prevention

Two hardening fixes applied to existing code:

1. **Vault paths** — Replace the weak `".." in folder` string check with `safe_resolve()` using `Path.resolve()` + `.relative_to()`. This handles edge cases like encoded characters, symlinks, and normalisation bypasses.

2. **Session IDs** — Validate that `session_id` matches UUID format before constructing file paths. Currently `chat_store.py` passes raw session IDs into `os.path.join()` — works because we generate UUIDs, but once WhatsApp sessions arrive (with `wa-` prefixed IDs), this needs explicit validation.

### Secrets Management

Secrets are stored as **files** in `deployment/secrets/` (chmod 600), never as environment variables.

Why files, not env vars:
- `docker inspect` dumps all env vars in plaintext
- Process listings (`/proc/*/environ`) expose env vars
- Logging frameworks often capture env vars in crash reports
- Files with restrictive permissions are only readable by the container process

The `deployment/secrets/` directory is gitignored. The only secret in Phase 1 is the Anthropic API key (which becomes unnecessary once OpenClaw is primary in Phase 2).

### Audit Trail

Every write operation (POST, PATCH, DELETE) is logged to `/logs/audit.log` as JSON Lines:

```json
{"timestamp": "2026-02-27T14:30:00Z", "method": "POST", "path": "/api/vault/write", "user": "web_ui", "status": 200, "ip": "172.17.0.1"}
{"timestamp": "2026-02-27T14:31:00Z", "method": "POST", "path": "/api/chat", "user": "whatsapp", "status": 200, "ip": "172.17.0.1"}
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

### WhatsApp Channel Restrictions (Phase 3)

The WhatsApp integration is the most exposed surface. It faces the public internet (via Cloudflare tunnel) and accepts messages from an external platform. Extra precautions:

| Layer | Control |
|-------|---------|
| Cloudflare tunnel | TLS termination, DDoS protection, no direct IP exposure |
| Non-guessable webhook path | Include a UUID segment in the webhook URL (e.g. `/webhook/<uuid>`) — reduces random scanning |
| Cloudflare rate limit | Global rate limit on the webhook path at Cloudflare level — stops volumetric abuse before it hits n8n |
| HMAC-SHA256 | Every webhook payload verified against WhatsApp app secret |
| Timestamp check | Reject messages older than 5 minutes (prevents replay attacks) |
| **Sender allowlist** | Only whitelisted phone numbers are processed — all others silently dropped (empty by default) |
| Rate limiting | 10 messages per minute per sender (prevents spam/abuse) |
| Field validation | Required fields checked before any processing |
| Bearer token | n8n → Athena calls use a scoped `whatsapp` token (chat-only) |
| Text-only responses | WhatsApp never sees `<graph_updates>`, node data, or internal metadata |
| Session isolation | Each phone number gets its own session (hashed, not stored raw) |
| Session cap | Max 100 messages per WhatsApp session before auto-reset |

**n8n admin UI isolation:** The Cloudflare tunnel must only expose the webhook endpoint path — **never the n8n admin UI**. If the n8n UI is accessible, an attacker can create/modify workflows, call arbitrary HTTP endpoints, and exfiltrate data. The n8n UI should only be reachable from `localhost:5678` directly. n8n must have built-in authentication enabled with a strong password.

Even if someone compromises the WhatsApp webhook entirely, the worst they can do is send chat messages to Athena. They cannot read the graph, write nodes, access insights, or reach any other endpoint.

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
Phase 1: Containerise + Harden ──→ Phase 2: OpenClaw ──→ Phase 3: n8n + WhatsApp
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
  whatsapp:
    endpoints: ["chat"]          # Chat only — no vault writes, no graph reads

# Claude API routing
claude:
  base_url: null                 # null = direct API. Set to OpenClaw gateway URL in Phase 2.
  fallback_url: "https://api.anthropic.com"
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
├── anthropic_api_key.txt        # Current API key (replaced by OpenClaw in Phase 2)
└── auth_tokens.yaml             # Bearer tokens → user mapping
```

`auth_tokens.yaml`:
```yaml
# Generate tokens with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
tokens:
  "REPLACE_WITH_GENERATED_TOKEN_1": "web_ui"
  "REPLACE_WITH_GENERATED_TOKEN_2": "n8n"        # Phase 3
  "REPLACE_WITH_GENERATED_TOKEN_3": "whatsapp"   # Phase 3
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
| `chat_store.py` | Validate `session_id` is UUID format in `_session_path()` — reject anything that isn't `[a-f0-9-]` or the `wa-` prefix (Phase 3). |
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

## Phase 2: OpenClaw Integration

### Goal

Route all Claude API calls through OpenClaw gateway, using Claude subscription instead of per-token billing. Streaming, sync, insights, and suggest-links all go through the same path.

### How It Works

```
Athena container (port 5001)
    │
    │  Anthropic Python SDK with custom base_url
    ▼
host.docker.internal:18789 ─── OpenClaw gateway (native on host, launchd)
    │
    │  HTTPS
    ▼
Claude API (billed to subscription, not per-token)
```

The Anthropic Python SDK supports a `base_url` parameter on `anthropic.Anthropic()`. Both `messages.create()` and `messages.stream()` use the same HTTP transport, so streaming works unchanged when routed through a gateway.

### Host Setup (not containerised — OpenClaw is infrastructure)

```bash
# Requires Node 22+
# IMPORTANT: Pin to exact version — never use @latest
# Check current version at https://www.npmjs.com/package/openclaw
npm install -g openclaw@X.Y.Z

# Verify package integrity after install
npm ls -g openclaw               # Confirm installed version
lsof -i -P | grep openclaw       # Confirm no unexpected network connections

# Authenticate with Claude subscription
openclaw models auth paste-token --provider anthropic

# Start gateway (registers launchd service)
openclaw gateway start    # Binds to 127.0.0.1:18789

# Verify
openclaw health
curl http://localhost:18789/api/health
```

### New Files

#### `backend/claude_client.py`

Shared client factory — single source of truth for all Claude API access.

```python
"""Shared Claude client with OpenClaw gateway support and fallback."""

import logging
import anthropic

logger = logging.getLogger(__name__)

def create_claude_client(config: dict) -> anthropic.Anthropic | None:
    """Create an Anthropic client, routing through OpenClaw if configured."""
    claude_config = config.get("claude", {})
    base_url = claude_config.get("base_url")
    api_key = config.get("_secrets", {}).get("anthropic_api_key", "")

    if base_url:
        logger.info(f"Claude client via gateway: {base_url}")
        return anthropic.Anthropic(
            base_url=base_url,
            api_key=api_key or "openclaw-gateway",
        )

    # Direct API fallback
    if not api_key:
        logger.error("No Claude API key and no gateway configured — Claude disabled")
        return None

    fallback = claude_config.get("fallback_url", "https://api.anthropic.com")
    logger.info(f"Claude client via direct API: {fallback}")
    return anthropic.Anthropic(base_url=fallback, api_key=api_key)
```

### Modified Files

| File | Change |
|------|--------|
| `server.py` | Import `create_claude_client`, create shared client at boot, pass to MentorAgent, store on `app.config["claude_client"]` |
| `mentor_agent.py` | Accept injected `client` parameter in `__init__` instead of creating `anthropic.Anthropic()` (currently line 356). Remove self-initialisation. |
| `insights_routes.py` | Replace standalone `client = anthropic.Anthropic()` (line 40) with `current_app.config["claude_client"]`. Add null check → 503. |
| `graph_routes.py` | Replace standalone `client = anthropic.Anthropic()` (line 181) with `current_app.config["claude_client"]`. Add null check → 503. |
| `chat_service.py` | Add `ConnectionError` / `httpx.ConnectError` handling for gateway being down. |
| `config.yaml` | Set `claude.base_url: "http://host.docker.internal:18789"` |

### Security

- OpenClaw gateway bound to loopback only (`127.0.0.1:18789`) — not exposed to network
- Container accesses it via Docker's `host.docker.internal` bridge
- No Anthropic API key stored in the container once OpenClaw is primary
- Subscription credential lives only in `~/.openclaw/openclaw.json` on host
- Gateway health checked before first API call

### Verification Checklist

- [ ] `openclaw health` shows gateway running
- [ ] Chat message works — logs show `"Claude client via gateway"`
- [ ] Streaming (`/api/chat/stream`) returns SSE tokens through the gateway
- [ ] Insights (`GET /api/insights`) returns analysis through the gateway
- [ ] Suggest-links (`POST /api/graph/suggest-links`) works through the gateway
- [ ] Stop gateway → next message returns graceful error (not crash)
- [ ] Restart gateway → works again immediately
- [ ] No `ANTHROPIC_API_KEY` present in container env (`docker exec athena env | grep ANTHROP`)

### Rollback

Set `claude.base_url: null` in `config.yaml` → reverts to direct API using key from `deployment/secrets/anthropic_api_key.txt`. Single config change, no code changes.

---

## Phase 3: n8n + WhatsApp

### Goal

Text Athena from WhatsApp, get text responses back. n8n handles webhook routing and validation. Cloudflare tunnel provides secure external exposure.

### Architecture

```
Phone (WhatsApp)
    │
    ▼
Meta Cloud API ─── webhook POST ───▶ Cloudflare Tunnel (TLS + DDoS protection)
                                          │
                                          ▼
                                     n8n (port 5678)
                                     ├─ Validate: HMAC-SHA256 signature
                                     ├─ Validate: timestamp freshness (<5 min)
                                     ├─ Validate: sender on allowlist
                                     ├─ Validate: required fields
                                     ├─ Rate limit: 10 msg/min per sender
                                     │
                                     ▼
                                Athena API (bearer token auth)
                                POST /api/chat/simple
                                     │
                                     ▼
                                Format response (text only, ≤4000 chars)
                                     │
                                     ▼
                                Send reply via WhatsApp Cloud API
```

### Threat Model (WhatsApp-specific)

| Threat | Mitigation |
|--------|-----------|
| Unauthorised webhook calls | HMAC-SHA256 verification using WhatsApp app secret |
| Replay attacks | Timestamp freshness check (reject >5 min old) |
| DoS / spam | Rate limiting (10 msg/min per sender), Cloudflare protection |
| Malformed payloads | Required field validation before processing |
| Cross-user session leakage | Sessions keyed by SHA-256 hash of phone number |
| Graph update exposure to WhatsApp | Dedicated `/api/chat/simple` returns text only |
| Unbounded session growth | Max message cap per WhatsApp session (auto-reset) |
| n8n → Athena spoofing | Bearer token auth on internal calls |

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
      - NODE_ENV=production
    volumes:
      - ~/athena/n8n-data:/home/node/.n8n
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
    # n8n needs outbound to Meta WhatsApp API (graph.facebook.com) via n8n-net.
    # Cannot use internal:true because it must reach Meta's API directly.
    # Egress is restricted at the host firewall level instead (see below).
  athena-net:
    external: true
    name: athena_athena-net
    # Joins the internal network created by the main docker-compose.
    # Gives n8n direct access to athena:5001 without publishing ports.
    # Requires the main Athena stack to be running first.
```

**n8n egress restriction (host firewall):**

n8n needs outbound internet for Meta's WhatsApp Cloud API (`graph.facebook.com`), so `n8n-net` cannot use Docker `internal: true`. Instead, restrict n8n's internet egress at the host level:

```bash
# /etc/pf.conf anchor for n8n egress
# Allow n8n container subnet (on n8n-net) to reach only:
#   1. graph.facebook.com (Meta WhatsApp Cloud API, resolve IPs)
#   2. DNS (port 53, required for hostname resolution)
#
# n8n reaches Athena directly at athena:5001 via athena-net (internal Docker
# network) — no host firewall rule needed for that path.

# Block everything from n8n subnet by default
block drop quick on bridge100 proto tcp from <n8n_subnet> to any
# Allow DNS
pass quick on bridge100 proto {tcp, udp} from <n8n_subnet> to any port 53
# Allow Meta API (update IPs periodically: dig +short graph.facebook.com)
pass quick on bridge100 proto tcp from <n8n_subnet> to <meta_api_ips> port 443
```

**Note:** This is a defense-in-depth measure. Even without the firewall, n8n's damage is limited — it has no vault mount, no API keys, and Athena's bearer token only grants `chat` access. n8n reaches Athena over the internal Docker network (not through the host), so the firewall only governs n8n's internet-facing traffic. The firewall prevents n8n from scanning your LAN or reaching other local services if compromised.

#### n8n Workflow (configured in UI, exported as JSON)

Nodes:
1. **WhatsApp Trigger** — webhook receives POST from Meta Cloud API
2. **Validate HMAC** — Code node: SHA-256 signature check using `X-Hub-Signature-256` header
3. **Validate Freshness** — Code node: reject messages older than 5 minutes
4. **Sender Allowlist** — Code node: check sender phone number against allowlist, silently drop if not whitelisted (allowlist configured in n8n credentials, starts empty)
5. **Rate Limit** — Code node: track per-sender message count, reject if >10/min
6. **Route to Athena** — HTTP Request: `POST http://athena:5001/api/chat/simple` with bearer token (via `athena-net`)
7. **Format Response** — Code node: strip to plain text, truncate to 4000 chars
8. **Send Reply** — WhatsApp Cloud API: send text message back to sender

#### New Secrets

```
deployment/secrets/
├── anthropic_api_key.txt        # Phase 1
├── auth_tokens.yaml             # Phase 1 — bearer tokens for web_ui, n8n, whatsapp
├── proxy_auth.conf              # Phase 1 — nginx include, injects web_ui bearer token
├── whatsapp_verify_token.txt    # Webhook verification challenge
├── whatsapp_access_token.txt    # Meta Cloud API access token
└── whatsapp_app_secret.txt      # HMAC signature verification
```

### Modified Files

| File | Change |
|------|--------|
| `chat_store.py` | Add `create_session_with_id(session_id)` for stable WhatsApp sessions (keyed by `wa-{phone_hash}`). Add max message count check. |
| `chat_routes.py` | Add `POST /api/chat/simple` — text-only response, no graph_updates, no relevant_nodes. For WhatsApp and other text-only clients. |
| `config.yaml` | Add `whatsapp:` section: `max_response_length: 4000`, `session_prefix: "wa-"`, `max_session_messages: 100`, `allowed_senders: []` (empty — no one can message until a number is added). **Set `auth.protect_reads: true`** — now that n8n exists as a separate container, read endpoints must require auth to prevent unauthenticated graph scraping. |

### Infrastructure Setup

1. Install cloudflared: `brew install cloudflare/cloudflare/cloudflared`
2. Login: `cloudflared tunnel login`
3. Create tunnel: `cloudflared tunnel create athena-webhook`
4. Configure ingress (see below — **critical: restrict to webhook path only**)
5. Start tunnel: `cloudflared tunnel run athena-webhook` (or launchd service)
6. Register webhook URL in Meta Developer Dashboard
7. Requires: WhatsApp Business account + Meta Developer app

#### Cloudflare Tunnel Ingress Config

**CRITICAL:** The tunnel must only expose the webhook endpoint — **never the n8n admin UI**. Without path restriction, the entire n8n instance (workflow editor, credentials, execution history) is publicly accessible.

`~/.cloudflared/config.yml`:
```yaml
tunnel: athena-webhook
credentials-file: /Users/mattwilson/.cloudflared/<tunnel-id>.json

ingress:
  # Only allow the WhatsApp webhook path (UUID segment prevents scanning)
  - hostname: athena-webhook.YOUR_DOMAIN.com
    path: /webhook/<uuid>
    service: http://localhost:5678
  # Reject everything else with 404
  - service: http_status:404
```

The `path` field restricts which URLs the tunnel will proxy. Any request that doesn't match `/webhook/<uuid>` gets a 404 from Cloudflare itself — it never reaches n8n. This means:
- `/` → 404 (n8n UI not exposed)
- `/rest/` → 404 (n8n API not exposed)
- `/webhook/wrong-path` → 404
- `/webhook/<uuid>` → forwarded to n8n (only valid path)

### Verification Checklist

- [ ] n8n starts: `cd deployment/n8n && docker compose up -d`
- [ ] n8n health: `curl localhost:5678/healthz` returns 200
- [ ] Cloudflare tunnel running, URL accessible externally
- [ ] Meta webhook verification succeeds (GET challenge)
- [ ] Send "Hello" from WhatsApp → get Athena text response
- [ ] Invalid HMAC signature → rejected (no forwarding to Athena)
- [ ] Message from non-allowlisted number → silently dropped
- [ ] 15 rapid messages → rate limited after 10
- [ ] Restart n8n → webhook still works
- [ ] Web UI completely unaffected by WhatsApp changes
- [ ] WhatsApp response contains no `<graph_updates>` blocks

### Rollback

n8n is in a separate docker-compose — stop it without touching Athena. Disable webhook in Meta Developer Dashboard. Delete `wa-` prefixed session files if needed. Zero impact to web UI.

---

## File Summary

| Phase | New Files | Modified Files |
|-------|-----------|---------------|
| 1 | `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`, `deployment/nginx/nginx.conf`, `docker-compose.yml`, `deployment/config/config.yaml`, `deployment/secrets/`, `backend/middleware/{__init__,auth,audit,security}.py` | `server.py`, `vault_service.py`, `chat_store.py`, `api.js`, `.gitignore` |
| 2 | `backend/claude_client.py` | `server.py`, `mentor_agent.py`, `insights_routes.py`, `graph_routes.py`, `chat_service.py`, `config.yaml` |
| 3 | `deployment/n8n/docker-compose.yml`, n8n workflow (JSON export), new secrets | `chat_store.py`, `chat_routes.py`, `config.yaml` |

---

## Open Questions

- WhatsApp Business: virtual number (can't share personal number — it deregisters from regular WhatsApp)
- Node 22 available on this machine? (required for OpenClaw)

---

## Principles

Borrowed from the reference project's containment philosophy:

1. **Containment by architecture, not trust** — security enforced by code structure (capabilities, path resolution, token validation), not by hoping things behave
2. **Secrets as files, never env vars** — prevents exposure in Docker inspect, logs, process listings
3. **Audit everything that mutates** — if it writes to the vault or changes a session, it gets logged
4. **Each phase is independently reversible** — if OpenClaw breaks, revert to direct API. If WhatsApp breaks, stop n8n. No cascading failures.
5. **Text-only for external channels** — WhatsApp gets Athena's words but never graph update proposals, node data, or internal metadata
