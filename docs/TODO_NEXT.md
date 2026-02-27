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
- ~~Chat ID allowlist~~ Done — 403 for unknown IDs
- Rate limiting — no per-session rate limit beyond max_session_messages cap
- Webhook secret token — Telegram supports `secret_token` on webhooks, n8n may not expose this

## Activity Feed
- Nodes created/updated via Telegram confirm flow may not show in desktop activity until page refresh
- Frontend should auto-refresh activity after graph changes or poll periodically

## Graph Organisation
- Node organisation not fully dialled — review how types/domains are categorised
- Review cross-referencing quality

## Pending
- ~~Commit all Phase 3 changes~~ Done
