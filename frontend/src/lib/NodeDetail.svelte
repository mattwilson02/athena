<script>
  let { node, neighbors = [], onClose = () => {}, onNodeClick = () => {} } = $props();

  const TYPE_COLORS = {
    goal: '#4ade80', fear: '#f87171', person: '#60a5fa', book: '#fbbf24',
    skill: '#a78bfa', habit: '#34d399', value: '#f472b6', belief: '#fb923c',
    interest: '#22d3ee', experience: '#e879f9', daily: '#94a3b8',
  };

  const SKIP_KEYS = new Set(['id', 'type', 'title', 'content', 'filepath', 'tags']);

  function getMetaEntries(node) {
    return Object.entries(node).filter(
      ([key, val]) => !SKIP_KEYS.has(key) && val != null && val !== ''
    );
  }
</script>

<div class="node-detail">
  <div class="panel-header">
    <div class="header-left">
      <span class="type-badge" style="background: {TYPE_COLORS[node.type] || '#888'}">
        {node.type}
      </span>
      <h2>{node.title || node.id}</h2>
    </div>
    <button class="close-btn" onclick={onClose}>x</button>
  </div>

  {#if node.tags?.length}
    <div class="tags">
      {#each (Array.isArray(node.tags) ? node.tags : [node.tags]) as tag}
        <span class="tag">{tag}</span>
      {/each}
    </div>
  {/if}

  <div class="metadata">
    {#each getMetaEntries(node) as [key, value]}
      <div class="meta-row">
        <span class="meta-key">{key}</span>
        <span class="meta-value">{value}</span>
      </div>
    {/each}
  </div>

  {#if node.content}
    <div class="content">
      {node.content}
    </div>
  {/if}

  {#if neighbors.length > 0}
    <div class="neighbors-section">
      <h3>Connected Nodes</h3>
      <ul class="neighbors">
        {#each neighbors as neighbor}
          <li>
            <button class="neighbor-link" onclick={() => onNodeClick(neighbor.id)}>
              <span class="type-dot" style="background: {TYPE_COLORS[neighbor.type] || '#888'}"></span>
              <span>{neighbor.title || neighbor.id}</span>
              <span class="neighbor-type">{neighbor.type}</span>
            </button>
          </li>
        {/each}
      </ul>
    </div>
  {/if}
</div>

<style>
  .node-detail {
    position: absolute;
    top: 0;
    right: 0;
    width: 360px;
    height: 100%;
    background: var(--bg-surface);
    border-left: 1px solid var(--border);
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    z-index: 10;
  }

  .panel-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
  }

  .header-left {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .type-badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 10px;
    border-radius: 10px;
    color: #0f0f1a;
    text-transform: uppercase;
    width: fit-content;
  }

  .panel-header h2 {
    font-size: 18px;
    font-weight: 600;
  }

  .close-btn {
    width: 28px;
    height: 28px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .close-btn:hover { background: var(--bg-surface-hover); }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .tag {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--bg-primary);
    color: var(--text-secondary);
  }

  .metadata {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .meta-row {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
  }

  .meta-key {
    color: var(--text-muted);
  }

  .meta-value {
    color: var(--text-primary);
    font-weight: 500;
  }

  .content {
    font-size: 13px;
    line-height: 1.6;
    color: var(--text-secondary);
    white-space: pre-wrap;
    word-wrap: break-word;
    padding: 12px;
    background: var(--bg-primary);
    border-radius: var(--radius);
    max-height: 300px;
    overflow-y: auto;
  }

  .neighbors-section h3 {
    font-size: 13px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
  }

  .neighbors {
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .neighbor-link {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 6px 8px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-primary);
    font-size: 13px;
    text-align: left;
  }

  .neighbor-link:hover { background: var(--bg-surface-hover); }

  .type-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .neighbor-type {
    margin-left: auto;
    font-size: 11px;
    color: var(--text-muted);
  }
</style>
