<script>
  import { writeNode, updateNode } from './api.js';

  let { update, onAccepted = () => {}, onDismissed = () => {} } = $props();
  let status = $state(update._alreadyInVault ? 'accepted' : 'pending');

  // Domain-based colour families
  const TYPE_COLORS = {
    // Self
    goal: '#4ade80', fear: '#f87171', belief: '#fb923c', value: '#f472b6',
    habit: '#34d399', skill: '#a78bfa',
    // People
    person: '#60a5fa', organisation: '#3b82f6',
    // Knowledge
    book: '#fbbf24', article: '#f59e0b', idea: '#eab308', note: '#a3a3a3',
    interest: '#22d3ee',
    // Life
    experience: '#e879f9', daily: '#94a3b8', memory: '#c084fc',
    // Planning
    task: '#38bdf8', project: '#0ea5e9', reminder: '#7dd3fc', event: '#06b6d4',
    // Places
    place: '#fb7185',
    // Finance
    expense: '#f97316', subscription: '#ea580c', budget: '#c2410c',
  };

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
    if (update.action === 'update') return '#f59e0b';
    return TYPE_COLORS[update.type] || '#888';
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
      onAccepted();
    } catch (e) {
      console.error('GraphUpdateCard accept error:', e);
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
    await writeNode({
      node_id: update.node_id,
      title: update.title,
      type: update.type,
      content: update.content || '',
      frontmatter: fm,
    });
  }

  async function acceptUpdate() {
    await updateNode(update.node_id, update.changes || {});
  }

  async function acceptLink() {
    // Add the edge to the source node
    await updateNode(update.source, {
      add_edges: [{ target: update.target, type: update.type || 'relates_to' }],
    });
  }

  async function mergeIntoExisting() {
    // Convert create into an update on the existing node
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
      onAccepted();
    } catch (e) {
      console.error('GraphUpdateCard merge error:', e);
      status = 'error';
    }
  }

  function dismiss() {
    status = 'dismissed';
    onDismissed();
  }
</script>

{#if status !== 'dismissed'}
  <div class="update-card" class:accepted={status === 'accepted'} class:error={status === 'error'}>
    <div class="card-header">
      <span class="action-label">{ACTION_LABELS[update.action] || update.action}</span>
      <span class="type-badge" style="background: {badgeColor()}">{badgeText()}</span>
      <span class="card-title">{cardTitle()}</span>
    </div>

    {#if cardDescription()}
      <p class="card-content">{cardDescription()}</p>
    {/if}

    {#if hasDuplicate && status === 'pending'}
      <div class="dedup-warning">
        <span class="dedup-icon">~</span>
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
      <div class="card-status error-text">Failed to write</div>
    {/if}
  </div>
{/if}

<style>
  .update-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px;
    margin-top: 8px;
  }

  .update-card.accepted {
    border-color: #4ade8040;
    opacity: 0.7;
  }

  .update-card.error {
    border-color: #f8717140;
  }

  .card-header {
    display: flex;
    align-items: center;
    gap: 8px;
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
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    color: #0f0f1a;
    text-transform: uppercase;
  }

  .card-title {
    font-weight: 500;
    font-size: 14px;
  }

  .card-content {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 8px;
  }

  .dedup-warning {
    font-size: 12px;
    color: #f59e0b;
    background: #f59e0b10;
    border: 1px solid #f59e0b30;
    border-radius: 6px;
    padding: 6px 10px;
    margin-bottom: 8px;
  }

  .dedup-icon {
    font-weight: 700;
    margin-right: 4px;
  }

  .card-actions {
    display: flex;
    gap: 8px;
  }

  .btn-accept {
    padding: 5px 14px;
    border: none;
    border-radius: 6px;
    background: #4ade80;
    color: #0f0f1a;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .btn-accept:hover { background: #22c55e; }

  .btn-merge {
    padding: 5px 14px;
    border: none;
    border-radius: 6px;
    background: #f59e0b;
    color: #0f0f1a;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .btn-merge:hover { background: #d97706; }

  .btn-dismiss {
    padding: 5px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 12px;
    cursor: pointer;
  }

  .btn-dismiss:hover { background: var(--bg-surface-hover); }

  .card-status {
    font-size: 12px;
    color: var(--text-muted);
  }

  .accepted-text { color: #4ade80; }
  .error-text { color: #f87171; }
</style>
