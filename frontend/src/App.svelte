<script>
  import { listSessions, createSession } from './lib/api.js';
  import Sidebar from './lib/Sidebar.svelte';
  import ChatView from './lib/ChatView.svelte';
  import GraphView from './lib/GraphView.svelte';

  let currentView = $state('chat');
  let currentSessionId = $state(null);
  let sessions = $state([]);

  async function loadSessions() {
    try {
      const data = await listSessions();
      sessions = data.sessions;
      // Auto-select the most recent session, or create one
      if (sessions.length > 0 && !currentSessionId) {
        currentSessionId = sessions[0].id;
      } else if (sessions.length === 0) {
        await handleNewSession();
      }
    } catch {
      // Backend might not be up yet
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

  // Load sessions on mount
  $effect(() => {
    loadSessions();
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
  />
  <div class="main-content">
    {#if currentView === 'chat'}
      <ChatView
        sessionId={currentSessionId}
        onSessionUpdate={loadSessions}
      />
    {:else}
      <GraphView />
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
