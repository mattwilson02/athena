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

## Graph Organisation
- Node organisation not fully dialled — review how types/domains are categorised
- Review cross-referencing quality

## Pending
- Commit all Phase 3 changes (code + Telegram migration + regex fix)
