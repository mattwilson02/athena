<script>
  import { getGraphStats, deleteSession } from './api.js';

  let {
    currentView = 'chat',
    onViewChange = () => {},
    sessions = [],
    currentSessionId = null,
    onSessionSelect = () => {},
    onNewSession = () => {},
  } = $props();

  let stats = $state(null);

  const TYPE_COLORS = {
    goal: '#4ade80', fear: '#f87171', person: '#60a5fa', book: '#fbbf24',
    skill: '#a78bfa', habit: '#34d399', value: '#f472b6', belief: '#fb923c',
    interest: '#22d3ee', experience: '#e879f9', daily: '#94a3b8',
  };

  $effect(() => {
    getGraphStats().then(s => stats = s).catch(() => {});
  });

  function timeAgo(dateStr) {
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'now';
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    return `${days}d`;
  }
</script>

<aside class="sidebar">
  <div class="logo">
    <h1>Athena</h1>
  </div>

  <nav class="view-toggle">
    <button class:active={currentView === 'chat'} onclick={() => onViewChange('chat')}>
      Chat
    </button>
    <button class:active={currentView === 'graph'} onclick={() => onViewChange('graph')}>
      Graph
    </button>
  </nav>

  {#if currentView === 'chat'}
    <div class="sessions-section">
      <div class="sessions-header">
        <h4>Sessions</h4>
        <button class="btn-new" onclick={onNewSession}>+</button>
      </div>
      <div class="session-list">
        {#each sessions as session}
          <button
            class="session-item"
            class:active={session.id === currentSessionId}
            onclick={() => onSessionSelect(session.id)}
          >
            <span class="session-title">{session.title}</span>
            <span class="session-time">{timeAgo(session.updated)}</span>
          </button>
        {/each}
      </div>
    </div>
  {/if}

  {#if stats}
    <div class="stats">
      <div class="stat-row">
        <span>Nodes</span><span class="stat-value">{stats.total_nodes}</span>
      </div>
      <div class="stat-row">
        <span>Edges</span><span class="stat-value">{stats.total_edges}</span>
      </div>

      {#if stats.types && Object.keys(stats.types).length > 0}
        <h4>By Type</h4>
        {#each Object.entries(stats.types).sort((a, b) => b[1] - a[1]) as [type, count]}
          <div class="stat-row type-row">
            <span class="type-dot" style="background: {TYPE_COLORS[type] || '#888'}"></span>
            <span class="type-label">{type}</span>
            <span class="stat-value">{count}</span>
          </div>
        {/each}
      {/if}
    </div>
  {/if}
</aside>

<style>
  .sidebar {
    width: 220px;
    min-width: 220px;
    background: #0b0b16;
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: 20px 16px;
    gap: 16px;
    overflow-y: auto;
  }

  .logo h1 {
    font-size: 20px;
    font-weight: 700;
    color: var(--accent);
  }

  .view-toggle {
    display: flex;
    gap: 4px;
    background: var(--bg-surface);
    border-radius: var(--radius);
    padding: 4px;
  }

  .view-toggle button {
    flex: 1;
    padding: 8px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }

  .view-toggle button.active {
    background: var(--accent);
    color: white;
  }

  .sessions-section {
    display: flex;
    flex-direction: column;
    gap: 8px;
    flex: 1;
    min-height: 0;
  }

  .sessions-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .sessions-header h4 {
    font-size: 11px;
    text-transform: uppercase;
    color: var(--text-muted);
    letter-spacing: 0.5px;
  }

  .btn-new {
    width: 24px;
    height: 24px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    line-height: 1;
  }

  .btn-new:hover {
    background: var(--bg-surface);
    color: var(--text-primary);
  }

  .session-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    overflow-y: auto;
  }

  .session-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 8px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 13px;
    text-align: left;
    width: 100%;
    transition: background 0.1s;
  }

  .session-item:hover {
    background: var(--bg-surface);
  }

  .session-item.active {
    background: var(--bg-surface);
    color: var(--text-primary);
  }

  .session-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  .session-time {
    font-size: 11px;
    color: var(--text-muted);
    flex-shrink: 0;
  }

  .stats {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .stats h4 {
    font-size: 11px;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-top: 8px;
    letter-spacing: 0.5px;
  }

  .stat-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 13px;
    color: var(--text-secondary);
    padding: 2px 0;
  }

  .stat-value {
    font-weight: 600;
    color: var(--text-primary);
  }

  .type-row {
    gap: 8px;
    justify-content: flex-start;
  }

  .type-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .type-label {
    flex: 1;
  }
</style>
