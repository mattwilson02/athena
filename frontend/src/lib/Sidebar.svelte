<script>
  import { getGraphStats } from './api.js';

  let { currentView = 'chat', onViewChange = () => {} } = $props();
  let stats = $state(null);

  const TYPE_COLORS = {
    goal: '#4ade80', fear: '#f87171', person: '#60a5fa', book: '#fbbf24',
    skill: '#a78bfa', habit: '#34d399', value: '#f472b6', belief: '#fb923c',
    interest: '#22d3ee', experience: '#e879f9', daily: '#94a3b8',
  };

  $effect(() => {
    getGraphStats().then(s => stats = s).catch(() => {});
  });
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
    gap: 24px;
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
