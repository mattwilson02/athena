<script>
  import { listSessions, createSession, getSchema } from './lib/api.js';
  import Sidebar from './lib/Sidebar.svelte';
  import ChatView from './lib/ChatView.svelte';
  import GraphView from './lib/GraphView.svelte';

  let currentView = $state('chat');
  let currentSessionId = $state(null);
  let sessions = $state([]);
  let schema = $state(null);
  let graphFilter = $state(null); // {domain: 'Self'} or {type: 'goal'} or null

  async function loadSessions() {
    try {
      const data = await listSessions();
      sessions = data.sessions;
      if (sessions.length > 0 && !currentSessionId) {
        currentSessionId = sessions[0].id;
      } else if (sessions.length === 0) {
        await handleNewSession();
      }
    } catch {
      // Backend might not be up yet
    }
  }

  async function loadSchema() {
    try {
      schema = await getSchema();
    } catch {
      // Fallback if backend not ready
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

  $effect(() => {
    loadSessions();
    loadSchema();
  });
</script>

<div class="app-layout">
  <Sidebar
    {currentView}
    onViewChange={(v) => currentView = v}
    {sessions}
    {currentSessionId}
    onSessionSelect={handleSessionSelect}
    onNewSession={handleNewSession}
    {schema}
    {graphFilter}
    onFilterChange={handleFilterChange}
  />
  <div class="main-content">
    {#if currentView === 'chat'}
      <ChatView
        sessionId={currentSessionId}
        onSessionUpdate={loadSessions}
      />
    {:else}
      <GraphView {schema} filter={graphFilter} />
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
</style>
