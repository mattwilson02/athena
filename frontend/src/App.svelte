<script>
  import { listSessions, createSession, getSchema, getGraph } from './lib/api.js';
  import Sidebar from './lib/Sidebar.svelte';
  import ChatView from './lib/ChatView.svelte';
  import GraphView from './lib/GraphView.svelte';
  import SearchModal from './lib/SearchModal.svelte';
  import TimelineView from './lib/TimelineView.svelte';
  import AccountabilityView from './lib/AccountabilityView.svelte';

  let currentView = $state('chat');
  let currentSessionId = $state(null);
  let sessions = $state([]);
  let schema = $state(null);
  let graphFilter = $state(null);
  let selectedGraphNode = $state(null);
  let connectionError = $state(false);
  let showSearch = $state(false);
  let nodeMap = $state({});

  async function loadSessions() {
    try {
      const data = await listSessions();
      sessions = data.sessions;
      connectionError = false;
      if (sessions.length > 0 && !currentSessionId) {
        currentSessionId = sessions[0].id;
      } else if (sessions.length === 0) {
        await handleNewSession();
      }
    } catch {
      connectionError = true;
    }
  }

  async function loadSchema() {
    try {
      schema = await getSchema();
      connectionError = false;
    } catch {
      // Backend not ready
    }
  }

  async function loadNodeMap() {
    try {
      const graphData = await getGraph();
      const map = {};
      for (const n of graphData.nodes || []) {
        map[n.id] = n;
      }
      nodeMap = map;
    } catch {
      // Graph not ready
    }
  }

  async function handleNewSession() {
    const session = await createSession();
    currentSessionId = session.id;
    sessions = [{ ...session, message_count: 0 }, ...sessions];
  }

  function handleSessionSelect(id) {
    currentSessionId = id;
    currentView = 'chat';
  }

  function handleFilterChange(filter) {
    graphFilter = filter;
    currentView = 'graph';
  }

  function handleNodeSelect(nodeId) {
    selectedGraphNode = nodeId;
    currentView = 'graph';
  }

  function handleGlobalKeydown(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      showSearch = !showSearch;
    }
  }

  function handleSearchSelect(nodeId) {
    selectedGraphNode = nodeId;
    currentView = 'graph';
    showSearch = false;
  }

  $effect(() => {
    loadSessions();
    loadSchema();
    loadNodeMap();
  });

  $effect(() => {
    window.addEventListener('keydown', handleGlobalKeydown);
    return () => window.removeEventListener('keydown', handleGlobalKeydown);
  });
</script>

{#if showSearch}
  <SearchModal onSelect={handleSearchSelect} onClose={() => showSearch = false} />
{/if}

{#if connectionError}
  <div class="error-banner">
    Backend not reachable. Check the server is running and your auth token is set.
  </div>
{/if}

<div class="app-layout">
  <Sidebar
    {currentView}
    onViewChange={(v) => currentView = v}
    {sessions}
    {currentSessionId}
    onSessionSelect={handleSessionSelect}
    onNewSession={handleNewSession}
    onSessionsUpdate={loadSessions}
    {schema}
    {graphFilter}
    onFilterChange={handleFilterChange}
  />
  <div class="main-content">
    {#if currentView === 'chat'}
      <ChatView
        sessionId={currentSessionId}
        onSessionUpdate={() => { loadSessions(); loadNodeMap(); }}
        onNodeSelect={handleNodeSelect}
        {nodeMap}
      />
    {:else if currentView === 'graph'}
      <GraphView {schema} filter={graphFilter} selectedNode={selectedGraphNode} />
    {:else if currentView === 'timeline'}
      <TimelineView onNodeSelect={handleNodeSelect} />
    {:else if currentView === 'accountability'}
      <AccountabilityView />
    {/if}
  </div>
</div>

<style>
  .app-layout {
    display: flex;
    height: 100%;
    width: 100%;
  }

  .main-content {
    flex: 1;
    overflow: hidden;
    position: relative;
  }

  .error-banner {
    background: var(--error-soft);
    color: var(--error);
    text-align: center;
    padding: var(--space-sm) var(--space-md);
    font-size: var(--text-sm);
    font-weight: 500;
    border-bottom: 1px solid var(--error);
  }
</style>
