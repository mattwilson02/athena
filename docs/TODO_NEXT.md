# Next Session — Things to Fix

## Graph Updates (UI + Backend)
- Updates don't show what actually changed — need more descriptive text (e.g. "Added tag: cycling" or "Updated status: active → completed")
- Can't update node name/title through graph updates — currently only supports content/tags/edges
- Failed updates still show as "updated" after page refresh — need proper error handling + rollback in UI state
- Updates don't reflect in UI until page refresh — should update in-place immediately after accept

## Telegram Integration
- ~2/9 messages failing on the Send Message node (Bad Gateway / connection closed)
- Likely Cloudflare quick tunnel instability — investigate persistent tunnel or retry logic in n8n
- Consider adding retry on fail in n8n Send Message node settings

## n8n Hosting / Stability
- Workflow lost when volume wiped (`docker compose down -v`) — unacceptable
- Options to fix:
  1. **Export workflow JSON** — save to `deployment/n8n/workflow.json`, import on fresh start
  2. **n8n Cloud** — hosted, no tunnel needed, but adds a dependency
  3. **Cloudflare named tunnel** — persistent URL, no quick tunnel instability
- At minimum: export the current workflow and commit it so it's recoverable

## Telegram Security Review
- WhatsApp had HMAC-SHA256 webhook verification + timestamp freshness — we dropped all of that
- Current Telegram security:
  - Bot token is secret (stored in n8n creds, not in code)
  - n8n webhook URL contains a random path (hard to guess)
  - Bearer token auth on Athena's /api/chat/simple endpoint
- Missing / to evaluate:
  - **Chat ID allowlist** — restrict bot to only your Telegram user ID (currently anyone who finds the bot can talk to it)
  - **Rate limiting** — no per-session rate limit on the simple endpoint
  - **Webhook secret token** — Telegram supports `secret_token` header on webhooks (setWebhook param), n8n may not expose this
- Priority: chat ID allowlist is the big one — add your ID (1936233108) to config and reject others

## Graph Organisation
- Node organisation not fully dialled — review how types/domains are categorised
- Review cross-referencing quality

## Pending
- ~~Commit all Phase 3 changes~~ Done
