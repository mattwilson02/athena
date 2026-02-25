<script>
  import { getTypeColor, getDomainColor } from './colors.js';

  let { node, neighbors = [], schema = null, onClose = () => {}, onNodeClick = () => {} } = $props();

  const SKIP_KEYS = new Set(['id', 'type', 'title', 'content', 'filepath', 'tags']);

  function getMetaEntries(node) {
    return Object.entries(node).filter(
      ([key, val]) => !SKIP_KEYS.has(key) && val != null && val !== ''
    );
  }

  function getDomain(type) {
    if (!schema?.types?.[type]) return null;
    return schema.types[type].domain || null;
  }
</script>

<div class="node-detail">
  <div class="panel-header">
    <div class="header-left">
      {#if getDomain(node.type)}
        <span class="domain-badge" style="color: {getDomainColor(getDomain(node.type))}">
          {getDomain(node.type)}
        </span>
      {/if}
      <span class="type-badge" style="background: {getTypeColor(node.type)}">
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
        <span class="meta-value">{typeof value === 'object' ? JSON.stringify(value) : value}</span>
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
              <span class="type-dot" style="background: {getTypeColor(neighbor.type)}"></span>
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
    position: absolute; top: 0; right: 0; width: 360px; height: 100%;
    background: var(--bg-surface); border-left: 1px solid var(--border);
    overflow-y: auto; padding: 20px; display: flex; flex-direction: column;
    gap: 16px; z-index: 10;
  }

  .panel-header {
    display: flex; align-items: flex-start; justify-content: space-between;
  }

  .header-left { display: flex; flex-direction: column; gap: 6px; }

  .domain-badge {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .type-badge {
    display: inline-block; font-size: 11px; font-weight: 600;
    padding: 2px 10px; border-radius: 10px; color: #0f0f1a;
    text-transform: uppercase; width: fit-content;
  }

  .panel-header h2 { font-size: 18px; font-weight: 600; }

  .close-btn {
    width: 28px; height: 28px; border: 1px solid var(--border); border-radius: 6px;
    background: transparent; color: var(--text-secondary); font-size: 14px;
    display: flex; align-items: center; justify-content: center;
  }
  .close-btn:hover { background: var(--bg-surface-hover); }

  .tags { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag {
    font-size: 11px; padding: 2px 8px; border-radius: 10px;
    background: var(--bg-primary); color: var(--text-secondary);
  }

  .metadata { display: flex; flex-direction: column; gap: 4px; }
  .meta-row { display: flex; justify-content: space-between; font-size: 13px; }
  .meta-key { color: var(--text-muted); }
  .meta-value { color: var(--text-primary); font-weight: 500; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }

  .content {
    font-size: 13px; line-height: 1.6; color: var(--text-secondary);
    white-space: pre-wrap; word-wrap: break-word; padding: 12px;
    background: var(--bg-primary); border-radius: var(--radius);
    max-height: 300px; overflow-y: auto;
  }

  .neighbors-section h3 {
    font-size: 13px; color: var(--text-muted); text-transform: uppercase;
    letter-spacing: 0.5px; margin-bottom: 4px;
  }
  .neighbors { list-style: none; display: flex; flex-direction: column; gap: 2px; }
  .neighbor-link {
    display: flex; align-items: center; gap: 8px; width: 100%;
    padding: 6px 8px; border: none; border-radius: 6px; background: transparent;
    color: var(--text-primary); font-size: 13px; text-align: left;
  }
  .neighbor-link:hover { background: var(--bg-surface-hover); }
  .type-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .neighbor-type { margin-left: auto; font-size: 11px; color: var(--text-muted); }
</style>
