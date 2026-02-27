<script>
  import { writeNode, updateNode, dismissUpdate } from './api.js';
  import { getTypeColor } from './colors.js';

  let { update, sessionId = null, onAccepted = () => {}, onDismissed = () => {} } = $props();
  let status = $state(update._dismissed ? 'dismissed' : update._alreadyInVault ? 'accepted' : 'pending');
  let errorMsg = $state('');
  let suggestedLinks = $state([]);
  let collapsed = $state(status === 'accepted');

  const ACTION_LABELS = {
    create: 'New',
    update: 'Update',
    link: 'Link',
  };

  function badgeText() {
    if (update.action === 'link') return 'link';
    if (update.action === 'update') return 'update';
    return update.type || 'node';
  }

  function badgeColor() {
    if (update.action === 'link') return '#64748b';
    if (update.action === 'update') return 'var(--warning)';
    return getTypeColor(update.type);
  }

  function borderColor() {
    if (status === 'accepted') return 'var(--success)';
    if (status === 'writing') return 'var(--accent)';
    if (status === 'error') return 'var(--error)';
    return 'var(--border)';
  }

  function cardTitle() {
    if (update.action === 'link') {
      return `${update.source} → ${update.target} (${update.type || 'relates_to'})`;
    }
    if (update.action === 'update') {
      return `Update: ${update.node_id}`;
    }
    return update.title || update.node_id;
  }

  function cardDescription() {
    if (update.action === 'update' && update.changes) {
      const parts = [];
      if (update.changes.frontmatter) {
        const keys = Object.keys(update.changes.frontmatter);
        parts.push(`Set ${keys.join(', ')}`);
      }
      if (update.changes.append_content) {
        parts.push(`Add: "${update.changes.append_content.slice(0, 60)}..."`);
      }
      if (update.changes.add_tags?.length) {
        parts.push(`Tags: +${update.changes.add_tags.join(', ')}`);
      }
      if (update.changes.add_edges?.length) {
        parts.push(`${update.changes.add_edges.length} new link(s)`);
      }
      return parts.join(' · ') || 'Update node';
    }
    if (update.content) {
      return update.content.slice(0, 120) + (update.content.length > 120 ? '...' : '');
    }
    return '';
  }

  let hasDuplicate = $derived(update._duplicate && update._duplicate.match !== 'none');

  async function accept() {
    status = 'writing';
    try {
      if (update.action === 'create') {
        await acceptCreate();
      } else if (update.action === 'update') {
        await acceptUpdate();
      } else if (update.action === 'link') {
        await acceptLink();
      }
      status = 'accepted';
      collapsed = true;
      onAccepted();
    } catch (e) {
      console.error('GraphUpdateCard accept error:', e);
      errorMsg = e.message || 'Failed to write';
      status = 'error';
    }
  }

  async function acceptCreate() {
    const today = new Date().toISOString().split('T')[0];
    const fm = {
      id: update.node_id,
      type: update.type,
      title: update.title,
      created: today,
      updated: today,
      tags: update.tags || [],
      ...(update.frontmatter || {}),
    };
    const result = await writeNode({
      node_id: update.node_id,
      title: update.title,
      type: update.type,
      content: update.content || '',
      frontmatter: fm,
      edges: update.edges || [],
    });

    if (result.suggested_links?.length) {
      suggestedLinks = result.suggested_links.map(link => ({
        ...link,
        _status: 'pending',
      }));
    }
  }

  async function acceptUpdate() {
    await updateNode(update.node_id, update.changes || {});
  }

  async function acceptLink() {
    await updateNode(update.source, {
      add_edges: [{ target: update.target, type: update.type || 'relates_to' }],
    });
  }

  async function acceptSuggestedLink(link) {
    link._status = 'writing';
    suggestedLinks = [...suggestedLinks];
    try {
      await updateNode(link.source, {
        add_edges: [{ target: link.target, type: link.type || 'relates_to' }],
      });
      link._status = 'accepted';
      suggestedLinks = [...suggestedLinks];
    } catch (e) {
      console.error('Suggested link accept error:', e);
      link._status = 'error';
      suggestedLinks = [...suggestedLinks];
    }
  }

  function dismissSuggestedLink(link) {
    link._status = 'dismissed';
    suggestedLinks = [...suggestedLinks];
  }

  async function mergeIntoExisting() {
    status = 'writing';
    try {
      const existingId = update._duplicate.existing_id;
      const changes = {};
      if (update.content) {
        changes.append_content = update.content;
      }
      if (update.tags?.length) {
        changes.add_tags = update.tags;
      }
      if (update.frontmatter) {
        changes.frontmatter = update.frontmatter;
      }
      if (update.edges?.length) {
        changes.add_edges = update.edges;
      }
      await updateNode(existingId, changes);
      status = 'accepted';
      collapsed = true;
      onAccepted();
    } catch (e) {
      console.error('GraphUpdateCard merge error:', e);
      status = 'error';
    }
  }

  function dismiss() {
    status = 'dismissed';
    const key = update.node_id || update.source || `${update.action}-${update.title}`;
    if (sessionId && key) {
      dismissUpdate(sessionId, key).catch(() => {});
    }
    onDismissed();
  }
</script>

{#if status !== 'dismissed'}
  {#if collapsed && (status === 'accepted')}
    <button
      class="update-card collapsed"
      style="border-left-color: {borderColor()}"
      onclick={() => collapsed = false}
    >
      <div class="card-header">
        <span class="action-label">{ACTION_LABELS[update.action] || update.action}</span>
        <span class="type-badge" style="background: {badgeColor()}">{badgeText()}</span>
        <span class="card-title">{cardTitle()}</span>
        <span class="status-done">Done</span>
      </div>
    </button>
  {:else}
    <div
      class="update-card"
      class:writing={status === 'writing'}
      class:accepted={status === 'accepted'}
      class:error={status === 'error'}
      style="border-left-color: {borderColor()}"
    >
      <div class="card-header">
        <span class="action-label">{ACTION_LABELS[update.action] || update.action}</span>
        <span class="type-badge" style="background: {badgeColor()}">{badgeText()}</span>
        <span class="card-title">{cardTitle()}</span>
      </div>

      {#if cardDescription()}
        <p class="card-content">{cardDescription()}</p>
      {/if}

      {#if update.action === 'create' && update.edges?.length && status === 'pending'}
        <div class="proposed-edges">
          {#each update.edges as edge}
            <span class="edge-pill">{edge.type} → {edge.target}</span>
          {/each}
        </div>
      {/if}

      {#if hasDuplicate && status === 'pending'}
        <div class="dedup-warning">
          Similar node exists: <strong>{update._duplicate.existing_title}</strong> ({update._duplicate.existing_type})
        </div>
      {/if}

      {#if status === 'pending'}
        <div class="card-actions">
          {#if hasDuplicate}
            <button class="btn-merge" onclick={mergeIntoExisting}>Merge</button>
            <button class="btn-accept" onclick={accept}>Create New</button>
          {:else}
            <button class="btn-accept" onclick={accept}>Accept</button>
          {/if}
          <button class="btn-dismiss" onclick={dismiss}>Dismiss</button>
        </div>
      {:else if status === 'writing'}
        <div class="card-status">Writing to vault...</div>
      {:else if status === 'accepted'}
        <div class="card-status accepted-text">
          {update.action === 'update' ? 'Node updated' : update.action === 'link' ? 'Linked' : 'Added to vault'}
        </div>
      {:else if status === 'error'}
        <div class="card-status error-text">{errorMsg || 'Failed to write'}</div>
        <div class="card-actions">
          <button class="btn-accept" onclick={accept}>Retry</button>
          <button class="btn-dismiss" onclick={dismiss}>Dismiss</button>
        </div>
      {/if}
    </div>
  {/if}

  {#if suggestedLinks.length > 0}
    <div class="suggested-links">
      <div class="suggested-header">Suggested links</div>
      {#each suggestedLinks as link}
        {#if link._status !== 'dismissed'}
          <div class="link-suggestion" class:accepted={link._status === 'accepted'}>
            <span class="link-arrow">{link.source} → {link.target}</span>
            <span class="link-type">{link.type}</span>
            <span class="link-reason">{link.reason}</span>
            {#if link._status === 'pending'}
              <button class="btn-link-accept" onclick={() => acceptSuggestedLink(link)}>Link</button>
              <button class="btn-link-dismiss" onclick={() => dismissSuggestedLink(link)}>Skip</button>
            {:else if link._status === 'writing'}
              <span class="link-status">Linking...</span>
            {:else if link._status === 'accepted'}
              <span class="link-status accepted-text">Linked</span>
            {:else if link._status === 'error'}
              <span class="link-status error-text">Failed</span>
            {/if}
          </div>
        {/if}
      {/each}
    </div>
  {/if}
{/if}

<style>
  .update-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-left: 3px solid var(--border);
    border-radius: var(--radius);
    padding: 12px;
    margin-top: var(--space-sm);
    transition: all var(--transition-normal);
  }

  .update-card.accepted {
    opacity: 0.7;
  }

  .update-card.writing {
    animation: pulse-border 1.5s infinite;
  }

  .update-card.error {
    background: var(--error-soft);
  }

  .update-card.collapsed {
    padding: 8px 12px;
    cursor: pointer;
    opacity: 0.6;
    width: 100%;
    text-align: left;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-left: 3px solid var(--success);
    border-radius: var(--radius);
    transition: all var(--transition-fast);
  }

  .update-card.collapsed:hover {
    opacity: 0.85;
    background: var(--bg-surface-hover);
  }

  .update-card.collapsed .card-header {
    margin-bottom: 0;
  }

  @keyframes pulse-border {
    0%, 100% { border-left-color: var(--accent); }
    50% { border-left-color: transparent; }
  }

  .card-header {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    margin-bottom: 6px;
  }

  .action-label {
    font-size: 10px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 4px;
    background: var(--bg-surface-hover);
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .type-badge {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    color: #0f0f1a;
    text-transform: uppercase;
  }

  .card-title {
    font-weight: 500;
    font-size: var(--text-base);
    flex: 1;
  }

  .status-done {
    font-size: var(--text-xs);
    color: var(--success);
    font-weight: 500;
  }

  .card-content {
    font-size: var(--text-sm);
    color: var(--text-secondary);
    margin-bottom: var(--space-sm);
    line-height: 1.5;
  }

  .proposed-edges {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-bottom: var(--space-sm);
  }

  .edge-pill {
    font-size: 10px;
    padding: 2px 8px;
    border-radius: 4px;
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 500;
  }

  .dedup-warning {
    font-size: var(--text-sm);
    color: var(--warning);
    background: var(--warning-soft);
    border: 1px solid rgba(245, 158, 11, 0.2);
    border-radius: 6px;
    padding: 6px 10px;
    margin-bottom: var(--space-sm);
  }

  .card-actions {
    display: flex;
    gap: var(--space-sm);
  }

  .btn-accept {
    padding: 5px 14px;
    border: none;
    border-radius: 6px;
    background: var(--success);
    color: #0f0f1a;
    font-size: var(--text-sm);
    font-weight: 600;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .btn-accept:hover { background: #22c55e; transform: translateY(-1px); }

  .btn-merge {
    padding: 5px 14px;
    border: none;
    border-radius: 6px;
    background: var(--warning);
    color: #0f0f1a;
    font-size: var(--text-sm);
    font-weight: 600;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .btn-merge:hover { background: #d97706; transform: translateY(-1px); }

  .btn-dismiss {
    padding: 5px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: var(--text-sm);
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .btn-dismiss:hover { background: var(--bg-surface-hover); }

  .card-status {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  .accepted-text { color: var(--success); }
  .error-text { color: var(--error); }

  /* Suggested links */
  .suggested-links {
    margin-top: 6px;
    padding: var(--space-sm);
    background: var(--bg-surface);
    border: 1px dashed var(--border);
    border-radius: var(--radius);
  }

  .suggested-header {
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
  }

  .link-suggestion {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: 4px 0;
    font-size: var(--text-sm);
    flex-wrap: wrap;
  }

  .link-suggestion.accepted { opacity: 0.6; }

  .link-arrow { color: var(--text-primary); font-weight: 500; }

  .link-type {
    padding: 1px 6px;
    border-radius: 4px;
    background: rgba(100, 116, 139, 0.2);
    color: var(--text-muted);
    font-size: 10px;
  }

  .link-reason {
    color: var(--text-muted);
    font-size: var(--text-xs);
    flex: 1;
  }

  .link-status { font-size: var(--text-xs); color: var(--text-muted); }

  .btn-link-accept {
    padding: 2px 10px;
    border: none;
    border-radius: 4px;
    background: var(--success);
    color: #0f0f1a;
    font-size: var(--text-xs);
    font-weight: 600;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .btn-link-accept:hover { background: #22c55e; }

  .btn-link-dismiss {
    padding: 2px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: transparent;
    color: var(--text-muted);
    font-size: var(--text-xs);
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .btn-link-dismiss:hover { background: var(--bg-surface-hover); }
</style>
