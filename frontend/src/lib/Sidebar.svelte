<script>
  import { getGraphStats, getInsights, deleteSession, renameSession } from './api.js';
  import { getTypeColor, getDomainColor } from './colors.js';

  let {
    currentView = 'chat',
    onViewChange = () => {},
    sessions = [],
    currentSessionId = null,
    onSessionSelect = () => {},
    onNewSession = () => {},
    onSessionsUpdate = () => {},
    schema = null,
    graphFilter = null,
    onFilterChange = () => {},
  } = $props();

  let stats = $state(null);
  let insights = $state(null);
  let insightsLoading = $state(false);
  let statsCollapsed = $state(true);
  let editingSessionId = $state(null);
  let editingTitle = $state('');
  let confirmDeleteId = $state(null);

  $effect(() => {
    getGraphStats().then(s => stats = s).catch(() => {});
  });

  // Expand stats when in graph view
  $effect(() => {
    if (currentView === 'graph') statsCollapsed = false;
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

  function getSessionGroups() {
    if (!sessions.length) return [];
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
    const weekAgo = new Date(today); weekAgo.setDate(weekAgo.getDate() - 7);

    const groups = { Today: [], Yesterday: [], 'This Week': [], Older: [] };
    for (const s of sessions) {
      const d = new Date(s.updated);
      if (d >= today) groups.Today.push(s);
      else if (d >= yesterday) groups.Yesterday.push(s);
      else if (d >= weekAgo) groups['This Week'].push(s);
      else groups.Older.push(s);
    }
    return Object.entries(groups).filter(([, items]) => items.length > 0);
  }

  function startRename(session) {
    editingSessionId = session.id;
    editingTitle = session.title;
  }

  async function finishRename() {
    if (!editingSessionId || !editingTitle.trim()) {
      editingSessionId = null;
      return;
    }
    try {
      await renameSession(editingSessionId, editingTitle.trim());
      onSessionsUpdate();
    } catch { /* silent */ }
    editingSessionId = null;
  }

  function handleRenameKeydown(e) {
    if (e.key === 'Enter') finishRename();
    if (e.key === 'Escape') editingSessionId = null;
  }

  async function handleDelete(sessionId) {
    try {
      await deleteSession(sessionId);
      confirmDeleteId = null;
      onSessionsUpdate();
    } catch { /* silent */ }
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
    <div class="logo-icon">A</div>
    <h1>Athena</h1>
  </div>

  <nav class="view-toggle">
    <button class:active={currentView === 'chat'} onclick={() => onViewChange('chat')}>
      Chat
    </button>
    <button class:active={currentView === 'graph'} onclick={() => onViewChange('graph')}>
      Graph
    </button>
    <button class:active={currentView === 'timeline'} onclick={() => onViewChange('timeline')}>
      Activity
    </button>
    <button class:active={currentView === 'accountability'} onclick={() => onViewChange('accountability')}>
      Track
    </button>
  </nav>

  {#if currentView === 'chat'}
    <div class="sessions-section">
      <div class="sessions-header">
        <h4>Sessions</h4>
        <button class="btn-new" onclick={onNewSession} title="New session">+</button>
      </div>
      <div class="session-list">
        {#each getSessionGroups() as [groupName, groupSessions]}
          <div class="session-group-label">{groupName}</div>
          {#each groupSessions as session}
            <div
              class="session-item"
              class:active={session.id === currentSessionId}
            >
              {#if editingSessionId === session.id}
                <input
                  class="rename-input"
                  bind:value={editingTitle}
                  onkeydown={handleRenameKeydown}
                  onblur={finishRename}
                  autofocus
                />
              {:else}
                <button
                  class="session-btn"
                  onclick={() => onSessionSelect(session.id)}
                  ondblclick={() => startRename(session)}
                  title="Double-click to rename"
                >
                  <span class="session-title">{session.title}</span>
                  <span class="session-time">{timeAgo(session.updated)}</span>
                </button>
                {#if confirmDeleteId === session.id}
                  <button class="btn-confirm-delete" onclick={() => handleDelete(session.id)}>Delete?</button>
                  <button class="btn-cancel-delete" onclick={() => confirmDeleteId = null}>No</button>
                {:else}
                  <button
                    class="btn-delete"
                    onclick={() => confirmDeleteId = session.id}
                    title="Delete session"
                  >&times;</button>
                {/if}
              {/if}
            </div>
          {/each}
        {/each}
      </div>
    </div>
  {/if}

  <div class="stats-section">
    <button class="stats-toggle" onclick={() => statsCollapsed = !statsCollapsed}>
      <h4>Graph</h4>
      <span class="toggle-arrow" class:open={!statsCollapsed}></span>
    </button>

    {#if !statsCollapsed}
      {#if stats}
        <div class="stats">
          <div class="stat-row">
            <span>Nodes</span><span class="stat-value">{stats.total_nodes}</span>
          </div>
          <div class="stat-row">
            <span>Edges</span><span class="stat-value">{stats.total_edges}</span>
          </div>

          {#if stats.total_nodes > 0}
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
          {/if}
        </div>
      {:else}
        <div class="stats-skeleton">
          <div class="skeleton-line"></div>
          <div class="skeleton-line short"></div>
          <div class="skeleton-line"></div>
        </div>
      {/if}
    {/if}
  </div>

  {#if stats?.total_nodes > 0}
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
</aside>

<style>
  .sidebar {
    width: 260px;
    min-width: 260px;
    background: #0b0b16;
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: var(--space-lg) var(--space-md);
    gap: var(--space-md);
    overflow-y: auto;
  }

  .logo {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }

  .logo-icon {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--accent);
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 15px;
    font-weight: 700;
    flex-shrink: 0;
  }

  .logo h1 { font-size: var(--text-xl); font-weight: 700; color: var(--accent); }

  .view-toggle {
    display: flex; gap: 4px; background: var(--bg-surface);
    border-radius: var(--radius); padding: 4px;
  }

  .view-toggle button {
    flex: 1; padding: 8px; border: none; border-radius: 6px;
    background: transparent; color: var(--text-secondary);
    font-size: var(--text-sm); font-weight: 500; transition: all var(--transition-fast);
  }

  .view-toggle button.active { background: var(--accent); color: white; }
  .view-toggle button:hover:not(.active) { background: var(--bg-surface-hover); }

  /* ── Sessions ── */

  .sessions-section { display: flex; flex-direction: column; gap: var(--space-sm); flex: 1; min-height: 0; }
  .sessions-header { display: flex; align-items: center; justify-content: space-between; }
  .sessions-header h4 { font-size: var(--text-xs); text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; }

  .btn-new {
    width: 24px; height: 24px; border: 1px solid var(--border); border-radius: 6px;
    background: transparent; color: var(--text-secondary); font-size: 16px;
    display: flex; align-items: center; justify-content: center; padding: 0; line-height: 1;
    transition: all var(--transition-fast);
  }
  .btn-new:hover { background: var(--bg-surface); color: var(--text-primary); }

  .session-list { display: flex; flex-direction: column; gap: 1px; overflow-y: auto; }

  .session-group-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    padding: var(--space-sm) var(--space-sm) 2px;
    margin-top: var(--space-xs);
  }

  .session-group-label:first-child { margin-top: 0; }

  .session-item {
    display: flex;
    align-items: center;
    gap: 2px;
    border-radius: 6px;
    position: relative;
  }

  .session-item:hover .btn-delete { opacity: 1; }

  .session-btn {
    display: flex; align-items: center; justify-content: space-between; gap: var(--space-sm);
    padding: 7px var(--space-sm); border: none; border-radius: 6px; background: transparent;
    color: var(--text-secondary); font-size: var(--text-sm); text-align: left; width: 100%;
    transition: background var(--transition-fast); flex: 1; min-width: 0;
  }

  .session-btn:hover { background: var(--bg-surface); }
  .session-item.active .session-btn { background: var(--bg-surface); color: var(--text-primary); }

  .session-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .session-time { font-size: var(--text-xs); color: var(--text-muted); flex-shrink: 0; }

  .btn-delete {
    opacity: 0;
    width: 20px; height: 20px;
    border: none; border-radius: 4px;
    background: transparent; color: var(--text-muted);
    font-size: 14px; display: flex; align-items: center; justify-content: center;
    transition: all var(--transition-fast); flex-shrink: 0;
    padding: 0;
  }
  .btn-delete:hover { background: var(--error-soft); color: var(--error); }

  .btn-confirm-delete {
    padding: 2px 6px; border: none; border-radius: 4px;
    background: var(--error); color: white; font-size: 10px; font-weight: 600;
    flex-shrink: 0;
  }

  .btn-cancel-delete {
    padding: 2px 6px; border: 1px solid var(--border); border-radius: 4px;
    background: transparent; color: var(--text-muted); font-size: 10px;
    flex-shrink: 0;
  }

  .rename-input {
    width: 100%;
    padding: 6px var(--space-sm);
    border: 1px solid var(--accent);
    border-radius: 6px;
    background: var(--bg-surface);
    color: var(--text-primary);
    font-size: var(--text-sm);
    outline: none;
  }

  /* ── Stats ── */

  .stats-section {
    display: flex;
    flex-direction: column;
  }

  .stats-toggle {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border: none;
    background: transparent;
    padding: var(--space-xs) 0;
    color: var(--text-muted);
    cursor: pointer;
  }

  .stats-toggle h4 {
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .toggle-arrow {
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid var(--text-muted);
    transition: transform var(--transition-fast);
  }

  .toggle-arrow.open { transform: rotate(180deg); }

  .stats { display: flex; flex-direction: column; gap: 3px; padding-top: var(--space-xs); }

  .stat-row {
    display: flex; align-items: center; justify-content: space-between;
    font-size: var(--text-sm); color: var(--text-secondary); padding: 2px 0;
  }
  .stat-value { font-weight: 600; color: var(--text-primary); font-size: var(--text-sm); }

  .domain-row {
    display: flex; align-items: center; gap: var(--space-sm); width: 100%;
    padding: 5px 6px; border: none; border-radius: 6px; background: transparent;
    color: var(--text-primary); font-size: var(--text-sm); font-weight: 600;
    text-align: left; transition: background var(--transition-fast); cursor: pointer;
    margin-top: var(--space-xs);
  }
  .domain-row:hover { background: var(--bg-surface); }
  .domain-row.active { background: var(--bg-surface-hover); }

  .domain-dot { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
  .domain-label { flex: 1; }

  .type-row {
    display: flex; align-items: center; gap: var(--space-sm); width: 100%;
    padding: 3px 6px 3px 22px; border: none; border-radius: 4px;
    background: transparent; color: var(--text-secondary); font-size: var(--text-sm);
    text-align: left; transition: background var(--transition-fast); cursor: pointer;
  }
  .type-row:hover { background: var(--bg-surface); }
  .type-row.active { background: var(--bg-surface-hover); color: var(--text-primary); }

  .type-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .type-label { flex: 1; }

  .btn-clear-filter {
    margin-top: var(--space-sm); padding: 4px 10px; border: 1px solid var(--border);
    border-radius: 6px; background: transparent; color: var(--text-muted);
    font-size: var(--text-xs); cursor: pointer; align-self: flex-start;
    transition: all var(--transition-fast);
  }
  .btn-clear-filter:hover { background: var(--bg-surface); color: var(--text-primary); }

  /* ── Loading skeleton ── */

  .stats-skeleton {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
    padding: var(--space-sm) 0;
  }

  .skeleton-line {
    height: 12px;
    background: var(--bg-surface-hover);
    border-radius: 4px;
    animation: shimmer 1.5s infinite;
  }

  .skeleton-line.short { width: 60%; }

  @keyframes shimmer {
    0% { opacity: 0.5; }
    50% { opacity: 1; }
    100% { opacity: 0.5; }
  }

  /* ── Insights ── */

  .insights-section {
    padding-top: var(--space-sm);
    border-top: 1px solid var(--border);
  }

  .btn-insights {
    width: 100%; padding: 8px; border: 1px solid var(--border);
    border-radius: 6px; background: transparent; color: var(--text-secondary);
    font-size: var(--text-sm); font-weight: 500; cursor: pointer; transition: all var(--transition-fast);
  }
  .btn-insights:hover:not(:disabled) { background: var(--bg-surface); color: var(--accent); border-color: var(--accent); }
  .btn-insights:disabled { opacity: 0.5; cursor: wait; }

  .insights-text {
    font-size: var(--text-sm); line-height: 1.5; color: var(--text-secondary);
    margin-bottom: var(--space-sm); white-space: pre-wrap; word-wrap: break-word;
  }
</style>
