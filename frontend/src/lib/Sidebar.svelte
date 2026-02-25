<script>
  import { getGraphStats, getInsights } from './api.js';
  import { getTypeColor, getDomainColor } from './colors.js';

  let {
    currentView = 'chat',
    onViewChange = () => {},
    sessions = [],
    currentSessionId = null,
    onSessionSelect = () => {},
    onNewSession = () => {},
    schema = null,
    graphFilter = null,
    onFilterChange = () => {},
  } = $props();

  let stats = $state(null);
  let insights = $state(null);
  let insightsLoading = $state(false);

  $effect(() => {
    getGraphStats().then(s => stats = s).catch(() => {});
  });

  async function loadInsights() {
    insightsLoading = true;
    try {
      const data = await getInsights();
      insights = data.insights;
    } catch {
      insights = 'Could not reach Athena.';
    } finally {
      insightsLoading = false;
    }
  }

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

  function getDomainStats() {
    if (!schema || !stats?.types) return [];
    const result = [];
    for (const domainName of schema.domain_list || []) {
      const domainInfo = schema.domains[domainName];
      if (!domainInfo) continue;
      const types = (domainInfo.types || []).filter(t => stats.types[t]);
      const domainTotal = types.reduce((sum, t) => sum + (stats.types[t] || 0), 0);
      if (domainTotal === 0) continue;
      result.push({
        name: domainName,
        total: domainTotal,
        types: types.map(t => ({ name: t, count: stats.types[t] || 0 })),
      });
    }
    return result;
  }

  function isFilterActive(domain, type) {
    if (!graphFilter) return false;
    if (type) return graphFilter.type === type;
    return graphFilter.domain === domain && !graphFilter.type;
  }

  function toggleFilter(domain, type) {
    if (type && isFilterActive(domain, type)) {
      onFilterChange(null);
    } else if (type) {
      onFilterChange({ domain, type });
    } else if (isFilterActive(domain, null)) {
      onFilterChange(null);
    } else {
      onFilterChange({ domain });
    }
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

      {#if stats.total_nodes > 0}
        <h4>Domains</h4>
        {#each getDomainStats() as domain}
          <button
            class="domain-row"
            class:active={isFilterActive(domain.name, null)}
            onclick={() => toggleFilter(domain.name, null)}
          >
            <span class="domain-dot" style="background: {getDomainColor(domain.name)}"></span>
            <span class="domain-label">{domain.name}</span>
            <span class="stat-value">{domain.total}</span>
          </button>
          {#each domain.types as type}
            <button
              class="type-row"
              class:active={isFilterActive(domain.name, type.name)}
              onclick={() => toggleFilter(domain.name, type.name)}
            >
              <span class="type-dot" style="background: {getTypeColor(type.name)}"></span>
              <span class="type-label">{type.name}</span>
              <span class="stat-value">{type.count}</span>
            </button>
          {/each}
        {/each}

        {#if graphFilter}
          <button class="btn-clear-filter" onclick={() => onFilterChange(null)}>
            Clear filter
          </button>
        {/if}

        <div class="insights-section">
          {#if insights}
            <p class="insights-text">{insights}</p>
            <button class="btn-insights" onclick={loadInsights} disabled={insightsLoading}>
              {insightsLoading ? 'Thinking...' : 'Refresh'}
            </button>
          {:else}
            <button class="btn-insights" onclick={loadInsights} disabled={insightsLoading}>
              {insightsLoading ? 'Thinking...' : 'What do I see?'}
            </button>
          {/if}
        </div>
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

  .logo h1 { font-size: 20px; font-weight: 700; color: var(--accent); }

  .view-toggle {
    display: flex; gap: 4px; background: var(--bg-surface);
    border-radius: var(--radius); padding: 4px;
  }

  .view-toggle button {
    flex: 1; padding: 8px; border: none; border-radius: 6px;
    background: transparent; color: var(--text-secondary);
    font-size: 13px; font-weight: 500; transition: all 0.15s;
  }

  .view-toggle button.active { background: var(--accent); color: white; }

  .sessions-section { display: flex; flex-direction: column; gap: 8px; flex: 1; min-height: 0; }
  .sessions-header { display: flex; align-items: center; justify-content: space-between; }
  .sessions-header h4 { font-size: 11px; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; }

  .btn-new {
    width: 24px; height: 24px; border: 1px solid var(--border); border-radius: 6px;
    background: transparent; color: var(--text-secondary); font-size: 16px;
    display: flex; align-items: center; justify-content: center; padding: 0; line-height: 1;
  }
  .btn-new:hover { background: var(--bg-surface); color: var(--text-primary); }

  .session-list { display: flex; flex-direction: column; gap: 2px; overflow-y: auto; }

  .session-item {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    padding: 8px; border: none; border-radius: 6px; background: transparent;
    color: var(--text-secondary); font-size: 13px; text-align: left; width: 100%;
    transition: background 0.1s;
  }
  .session-item:hover { background: var(--bg-surface); }
  .session-item.active { background: var(--bg-surface); color: var(--text-primary); }

  .session-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .session-time { font-size: 11px; color: var(--text-muted); flex-shrink: 0; }

  .stats { display: flex; flex-direction: column; gap: 4px; }
  .stats h4 { font-size: 11px; text-transform: uppercase; color: var(--text-muted); margin-top: 8px; letter-spacing: 0.5px; }

  .stat-row {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 13px; color: var(--text-secondary); padding: 2px 0;
  }
  .stat-value { font-weight: 600; color: var(--text-primary); font-size: 12px; }

  .domain-row {
    display: flex; align-items: center; gap: 8px; width: 100%;
    padding: 5px 6px; border: none; border-radius: 6px; background: transparent;
    color: var(--text-primary); font-size: 13px; font-weight: 600;
    text-align: left; transition: background 0.1s; cursor: pointer;
  }
  .domain-row:hover { background: var(--bg-surface); }
  .domain-row.active { background: var(--bg-surface-hover); }

  .domain-dot { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
  .domain-label { flex: 1; }

  .type-row {
    display: flex; align-items: center; gap: 8px; width: 100%;
    padding: 3px 6px 3px 20px; border: none; border-radius: 4px;
    background: transparent; color: var(--text-secondary); font-size: 12px;
    text-align: left; transition: background 0.1s; cursor: pointer;
  }
  .type-row:hover { background: var(--bg-surface); }
  .type-row.active { background: var(--bg-surface-hover); color: var(--text-primary); }

  .type-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .type-label { flex: 1; }

  .btn-clear-filter {
    margin-top: 6px; padding: 4px 10px; border: 1px solid var(--border);
    border-radius: 6px; background: transparent; color: var(--text-muted);
    font-size: 11px; cursor: pointer; align-self: flex-start;
  }
  .btn-clear-filter:hover { background: var(--bg-surface); color: var(--text-primary); }

  .insights-section {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }

  .btn-insights {
    width: 100%; padding: 8px; border: 1px solid var(--border);
    border-radius: 6px; background: transparent; color: var(--text-secondary);
    font-size: 12px; font-weight: 500; cursor: pointer; transition: all 0.15s;
  }
  .btn-insights:hover:not(:disabled) { background: var(--bg-surface); color: var(--accent); border-color: var(--accent); }
  .btn-insights:disabled { opacity: 0.5; cursor: wait; }

  .insights-text {
    font-size: 12px; line-height: 1.5; color: var(--text-secondary);
    margin-bottom: 8px; white-space: pre-wrap; word-wrap: break-word;
  }
</style>
