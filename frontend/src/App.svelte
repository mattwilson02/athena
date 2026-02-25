<script>
  import Sidebar from './lib/Sidebar.svelte';
  import ChatView from './lib/ChatView.svelte';
  import GraphView from './lib/GraphView.svelte';

  let currentView = $state('chat');
</script>

<div class="app-layout">
  <Sidebar {currentView} onViewChange={(v) => currentView = v} />
  <div class="main-content">
    {#if currentView === 'chat'}
      <ChatView />
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
