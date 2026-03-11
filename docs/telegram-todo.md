# Telegram Setup — Restart Checklist

Current state: n8n container is running, Athena backend is running, bot token is stored in n8n credentials. Missing: Cloudflare named tunnel.

## Steps

### 1. Create Cloudflare named tunnel

```bash
# Install (if not already)
brew install cloudflare/cloudflare/cloudflared

# Authenticate — opens browser to log in to your Cloudflare account
cloudflared tunnel login

# Create the tunnel (generates a credentials JSON file)
cloudflared tunnel create athena-webhook

# Add DNS record — point a subdomain to the tunnel
# Replace YOUR_DOMAIN with your actual Cloudflare domain
cloudflared tunnel route dns athena-webhook athena-webhook.YOUR_DOMAIN.com
```

### 2. Configure the tunnel

Create `~/.cloudflared/config.yml` (or wherever you prefer):

```yaml
tunnel: athena-webhook
credentials-file: /Users/mattwilson/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: athena-webhook.YOUR_DOMAIN.com
    path: /webhook/*
    service: http://localhost:5678
  - service: http_status:404    # Block everything else (n8n UI stays local-only)
```

The path restriction ensures only n8n's webhook endpoint is exposed — the admin UI at `/` is blocked.

### 3. Start the tunnel

```bash
# Run directly
cloudflared tunnel run athena-webhook

# Or install as a system service (survives reboots)
sudo cloudflared service install
```

### 4. Update n8n webhook URL

Set the stable tunnel hostname in `deployment/n8n/docker-compose.yml`:

```yaml
environment:
  - WEBHOOK_URL=https://athena-webhook.YOUR_DOMAIN.com/
```

Restart n8n:

```bash
cd deployment/n8n && docker compose restart
```

### 5. Verify n8n can reach Athena

```bash
# n8n health
curl localhost:5678/healthz

# n8n → Athena (via internal Docker network)
docker exec athena-n8n wget -qO- http://athena:5001/api/health
```

### 6. Verify Telegram webhook

Open n8n UI at `localhost:5678`, check the Telegram workflow is active. The Telegram trigger node auto-registers the webhook with Telegram's API when activated.

Send a test message to the bot on Telegram. Should get an Athena response back.

### 7. Verify confirm/dismiss flow

- Send a message that triggers a graph update (e.g. "I'm planning a trip to Berlin")
- Bot should respond with text + "Pending: create ..." prompt
- Reply "yes" → should save, reply "no" → should skip

## Security Reminders

- Tunnel ingress restricts to `/webhook/*` only — n8n admin UI never exposed to internet
- n8n UI only reachable from `localhost:5678`
- Chat ID allowlist in `deployment/config/config.yaml` → only `1936233108` is allowed
- Bot token stored encrypted in n8n (encrypted with `N8N_ENCRYPTION_KEY`)
- `telegram` bearer token has `chat` permission only — no vault writes, no graph reads

## Known Issues

- Webhook re-registration can be flaky after tunnel restart — toggle the workflow off/on in n8n UI if messages aren't arriving

## Reference

Full spec: [docs/V5_SPEC.md](V5_SPEC.md) (Phase 3 section, line 734+)
