<script>
  import { writeNode, updateNode, dismissUpdate, getNode } from './api.js';
  import { getTypeColor } from './colors.js';

  let { update, sessionId = null, nodeMap = {}, onNodeSelect = () => {}, onAccepted = () => {}, onDismissed = () => {} } = $props();
  const initialStatus = update._dismissed ? 'dismissed' : update._alreadyInVault ? 'accepted' : 'pending';
  let status = $state(initialStatus);
  let errorMsg = $state('');
  let suggestedLinks = $state([]);
  let collapsed = $state(initialStatus === 'accepted');
  let contentExpanded = $state(false);
  let currentNode = $state(null);

  const ACTION_LABELS = {
    create: 'New',
    update: 'Update',
    link: 'Link',
  };

  const HIDDEN_FM_KEYS = new Set(['id', 'type', 'title', 'created', 'updated', 'tags']);
  const CONTENT_PREVIEW_LENGTH = 200;

  // Fetch current node state for update cards (enables before → after)
  $effect(() => {
    if (update.action === 'update' && update.node_id) {
      getNode(update.node_id).then(n => currentNode = n).catch(() => {});
    }
  });

  function resolveTitle(id) {
    return nodeMap[id]?.title || id;
  }

  function badgeText() {
    if (update.action === 'link') return 'link';
    if (update.action === 'update') return currentNode?.type || 'update';
    return update.type || 'node';
  }

  function badgeColor() {
    if (update.action === 'link') return '#64748b';
    if (update.action === 'update') return getTypeColor(currentNode?.type);
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
      return `${resolveTitle(update.source)} → ${resolveTitle(update.target)}`;
    }
    if (update.action === 'update') {
      return currentNode?.title || resolveTitle(update.node_id);
    }
    return update.title || update.node_id;
  }

  // Get visible frontmatter fields (filter boilerplate)
  function getVisibleFrontmatter(fm) {
    if (!fm) return [];
    return Object.entries(fm).filter(([k]) => !HIDDEN_FM_KEYS.has(k));
  }

  // Get before value for a frontmatter key from current node
  function getBeforeValue(key) {
    if (!currentNode) return null;
    // Check direct node properties first, then frontmatter
    if (key in currentNode) return currentNode[key];
    if (currentNode.frontmatter && key in currentNode.frontmatter) return currentNode.frontmatter[key];
    return null;
  }

  let hasDuplicate = $derived(update._duplicate && update._duplicate.match !== 'none');
  let hasLongContent = $derived(update.content && update.content.length > CONTENT_PREVIEW_LENGTH);
  let displayContent = $derived(
    hasLongContent && !contentExpanded
      ? update.content.slice(0, CONTENT_PREVIEW_LENGTH) + '...'
      : update.content || ''
  );

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
      <div class="card-header" class:clickable={status === 'accepted'} onclick={() => { if (status === 'accepted') collapsed = true; }}>
        <span class="action-label">{ACTION_LABELS[update.action] || update.action}</span>
        <span class="type-badge" style="background: {badgeColor()}">{badgeText()}</span>
        <span class="card-title">{cardTitle()}</span>
        {#if update.action !== 'link' && (update.node_id || update.action === 'create')}
          <button class="btn-view-node" onclick={(e) => { e.stopPropagation(); onNodeSelect(update.node_id); }}>View</button>
        {/if}
        {#if status === 'accepted'}<span class="collapse-hint">Collapse</span>{/if}
      </div>

      <!-- CREATE card body -->
      {#if update.action === 'create'}
        {#if displayContent}
          <p class="card-content">{displayContent}</p>
          {#if hasLongContent}
            <button class="btn-expand" onclick={() => contentExpanded = !contentExpanded}>
              {contentExpanded ? 'Show less' : 'Show more'}
            </button>
          {/if}
        {/if}

        {#if getVisibleFrontmatter(update.frontmatter).length > 0}
          <div class="fm-grid">
            {#each getVisibleFrontmatter(update.frontmatter) as [key, value]}
              <div class="fm-field">
                <span class="fm-key">{key}</span>
                <span class="fm-value">{value}</span>
              </div>
            {/each}
          </div>
        {/if}

        {#if update.tags?.length}
          <div class="tag-row">
            {#each update.tags as tag}
              <span class="tag-pill">{tag}</span>
            {/each}
          </div>
        {/if}

        {#if update.edges?.length && status === 'pending'}
          <div class="edges-section">
            {#each update.edges as edge}
              <span class="edge-pill">{edge.type} → {resolveTitle(edge.target)}</span>
            {/each}
          </div>
        {/if}

      <!-- UPDATE card body -->
      {:else if update.action === 'update' && update.changes}
        <div class="changes-list">
          {#if update.changes.title}
            <div class="change-row">
              <span class="change-key">title</span>
              {#if currentNode?.title}
                <span class="change-before">{currentNode.title}</span>
                <span class="change-arrow">→</span>
              {/if}
              <span class="change-after">{update.changes.title}</span>
            </div>
          {/if}

          {#if update.changes.frontmatter}
            {#each Object.entries(update.changes.frontmatter) as [key, value]}
              <div class="change-row">
                <span class="change-key">{key}</span>
                {#if getBeforeValue(key) != null}
                  <span class="change-before">{getBeforeValue(key)}</span>
                  <span class="change-arrow">→</span>
                {/if}
                <span class="change-after">{value}</span>
              </div>
            {/each}
          {/if}

          {#if update.changes.content}
            <div class="change-block">
              <span class="change-key">content</span>
              <span class="change-warning">replaces existing</span>
              <p class="change-content">{update.changes.content}</p>
            </div>
          {/if}

          {#if update.changes.append_content}
            <div class="change-block">
              <span class="change-key">append</span>
              <p class="change-content">{update.changes.append_content}</p>
            </div>
          {/if}

          {#if update.changes.add_tags?.length}
            <div class="tag-row">
              {#each update.changes.add_tags as tag}
                <span class="tag-pill tag-add">+{tag}</span>
              {/each}
            </div>
          {/if}

          {#if update.changes.add_edges?.length}
            <div class="edges-section">
              {#each update.changes.add_edges as edge}
                <span class="edge-pill">+{edge.type} → {resolveTitle(edge.target)}</span>
              {/each}
            </div>
          {/if}
        </div>

      <!-- LINK card body -->
      {:else if update.action === 'link'}
        <div class="link-display">
          <span class="link-type-label">{update.type || 'relates_to'}</span>
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
            <span class="link-arrow">{resolveTitle(link.source)} → {resolveTitle(link.target)}</span>
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
    margin-bottom: 8px;
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

  .card-header.clickable {
    cursor: pointer;
    border-radius: 4px;
    padding: 2px 4px;
    margin: -2px -4px;
  }

  .card-header.clickable:hover {
    background: var(--bg-surface-hover);
  }

  .collapse-hint {
    font-size: var(--text-xs);
    color: var(--text-muted);
    opacity: 0;
    transition: opacity var(--transition-fast);
  }

  .card-header.clickable:hover .collapse-hint {
    opacity: 1;
  }

  .btn-view-node {
    font-size: var(--text-xs);
    color: var(--accent);
    background: none;
    border: 1px solid var(--accent-soft);
    border-radius: 4px;
    padding: 1px 8px;
    cursor: pointer;
    transition: all var(--transition-fast);
    flex-shrink: 0;
  }

  .btn-view-node:hover {
    background: var(--accent-soft);
  }

  /* ── Content ── */

  .card-content {
    font-size: var(--text-sm);
    color: var(--text-secondary);
    margin-bottom: var(--space-sm);
    line-height: 1.5;
    white-space: pre-wrap;
  }

  .btn-expand {
    font-size: var(--text-xs);
    color: var(--accent);
    background: none;
    border: none;
    padding: 0;
    margin-bottom: var(--space-sm);
    cursor: pointer;
  }

  .btn-expand:hover { text-decoration: underline; }

  /* ── Frontmatter grid ── */

  .fm-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: var(--space-sm);
  }

  .fm-field {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    background: var(--bg-surface-hover);
    border-radius: 4px;
    font-size: var(--text-xs);
  }

  .fm-key {
    color: var(--text-muted);
    font-weight: 500;
  }

  .fm-value {
    color: var(--text-primary);
    font-weight: 600;
  }

  /* ── Tags ── */

  .tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-bottom: var(--space-sm);
  }

  .tag-pill {
    font-size: 10px;
    padding: 2px 8px;
    border-radius: 4px;
    background: rgba(108, 99, 255, 0.1);
    color: var(--accent);
    font-weight: 500;
  }

  .tag-add {
    background: var(--success-soft);
    color: var(--success);
  }

  /* ── Edges ── */

  .edges-section {
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

  /* ── Changes (update cards) ── */

  .changes-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: var(--space-sm);
  }

  .change-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: var(--text-sm);
    flex-wrap: wrap;
  }

  .change-key {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-weight: 600;
    min-width: 50px;
  }

  .change-before {
    color: var(--text-muted);
    text-decoration: line-through;
  }

  .change-arrow {
    color: var(--text-muted);
    font-size: var(--text-xs);
  }

  .change-after {
    color: var(--text-primary);
    font-weight: 600;
  }

  .change-block {
    margin-bottom: 4px;
  }

  .change-warning {
    font-size: 10px;
    color: var(--warning);
    margin-left: 6px;
    font-weight: 500;
  }

  .change-content {
    font-size: var(--text-sm);
    color: var(--text-secondary);
    line-height: 1.5;
    margin-top: 4px;
    padding: 6px 8px;
    background: var(--bg-surface-hover);
    border-radius: 4px;
    white-space: pre-wrap;
  }

  /* ── Link cards ── */

  .link-display {
    margin-bottom: var(--space-sm);
  }

  .link-type-label {
    font-size: var(--text-xs);
    padding: 2px 8px;
    border-radius: 4px;
    background: rgba(100, 116, 139, 0.2);
    color: var(--text-muted);
    font-weight: 500;
  }

  /* ── Dedup warning ── */

  .dedup-warning {
    font-size: var(--text-sm);
    color: var(--warning);
    background: var(--warning-soft);
    border: 1px solid rgba(245, 158, 11, 0.2);
    border-radius: 6px;
    padding: 6px 10px;
    margin-bottom: var(--space-sm);
  }

  /* ── Actions ── */

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

  /* ── Suggested links ── */

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
