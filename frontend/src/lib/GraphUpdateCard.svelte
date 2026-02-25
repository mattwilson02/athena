<script>
  import { writeNode } from './api.js';

  let { update, onAccepted = () => {}, onDismissed = () => {} } = $props();
  let status = $state(update._alreadyInVault ? 'accepted' : 'pending');

  const TYPE_FOLDER_MAP = {
    goal: 'Goals', fear: 'Fears', person: 'People', book: 'Books',
    skill: 'Skills', habit: 'Habits', value: 'Values', belief: 'Beliefs',
    interest: 'Interests', experience: 'Experiences', daily: 'Daily',
  };

  const TYPE_COLORS = {
    goal: '#4ade80', fear: '#f87171', person: '#60a5fa', book: '#fbbf24',
    skill: '#a78bfa', habit: '#34d399', value: '#f472b6', belief: '#fb923c',
    interest: '#22d3ee', experience: '#e879f9', daily: '#94a3b8',
  };

  async function accept() {
    if (update.action !== 'create') {
      status = 'accepted';
      onAccepted();
      return;
    }

    status = 'writing';
    try {
      const today = new Date().toISOString().split('T')[0];
      await writeNode({
        node_id: update.node_id,
        title: update.title,
        type: update.type,
        folder: TYPE_FOLDER_MAP[update.type] || 'Goals',
        content: update.content || '',
        frontmatter: {
          id: update.node_id,
          type: update.type,
          title: update.title,
          created: today,
          updated: today,
          tags: update.tags || [],
        },
      });
      status = 'accepted';
      onAccepted();
    } catch (e) {
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
      <span class="type-badge" style="background: {TYPE_COLORS[update.type] || '#888'}">
        {update.action === 'link' ? 'link' : update.type}
      </span>
      <span class="card-title">{update.title || `${update.source} → ${update.target}`}</span>
    </div>

    {#if update.content}
      <p class="card-content">{update.content.slice(0, 120)}{update.content.length > 120 ? '...' : ''}</p>
    {/if}

    {#if status === 'pending'}
      <div class="card-actions">
        <button class="btn-accept" onclick={accept}>Accept</button>
        <button class="btn-dismiss" onclick={dismiss}>Dismiss</button>
      </div>
    {:else if status === 'writing'}
      <div class="card-status">Writing to vault...</div>
    {:else if status === 'accepted'}
      <div class="card-status accepted-text">Added to vault</div>
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
  }

  .btn-accept:hover { background: #22c55e; }

  .btn-dismiss {
    padding: 5px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .btn-dismiss:hover { background: var(--bg-surface-hover); }

  .card-status {
    font-size: 12px;
    color: var(--text-muted);
  }

  .accepted-text { color: #4ade80; }
  .error-text { color: #f87171; }
</style>
