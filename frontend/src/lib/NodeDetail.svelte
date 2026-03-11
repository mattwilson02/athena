<script>
  import { getTypeColor, getDomainColor } from './colors.js';
  import { formatText } from './format.js';
  import { updateNode } from './api.js';

  let { node, neighbors = [], schema = null, onClose = () => {}, onNodeClick = () => {} } = $props();

  const SKIP_KEYS = new Set(['id', 'type', 'title', 'content', 'filepath', 'tags']);

  // Edit mode state
  let isEditing = $state(false);
  let isSaving = $state(false);
  let editTitle = $state('');
  let editContent = $state('');
  let editTags = $state([]);
  let editMeta = $state({});
  let newTag = $state('');

  function startEditing() {
    editTitle = node.title || node.id;
    editContent = node.content || '';
    editTags = Array.isArray(node.tags) ? [...node.tags] : node.tags ? [node.tags] : [];
    editMeta = {};
    for (const [key, val] of getMetaEntries(node)) {
      editMeta[key] = typeof val === 'object' ? JSON.stringify(val) : String(val);
    }
    isEditing = true;
  }

  function cancelEditing() {
    isEditing = false;
    newTag = '';
  }

  function addTag() {
    const tag = newTag.trim();
    if (tag && !editTags.includes(tag)) {
      editTags = [...editTags, tag];
    }
    newTag = '';
  }

  function removeTag(tag) {
    editTags = editTags.filter(t => t !== tag);
  }

  async function saveEdits() {
    isSaving = true;
    const changes = {};
    const originalTags = Array.isArray(node.tags) ? node.tags : node.tags ? [node.tags] : [];

    if (editTitle !== (node.title || node.id)) changes.title = editTitle;
    if (editContent !== (node.content || '')) changes.content = editContent;

    const addedTags = editTags.filter(t => !originalTags.includes(t));
    const removedTags = originalTags.filter(t => !editTags.includes(t));
    if (addedTags.length) changes.add_tags = addedTags;
    if (removedTags.length) changes.remove_tags = removedTags;

    const fmChanges = {};
    for (const [key, val] of Object.entries(editMeta)) {
      const original = node[key];
      const originalStr = original == null ? '' : typeof original === 'object' ? JSON.stringify(original) : String(original);
      if (val !== originalStr) fmChanges[key] = val;
    }
    if (Object.keys(fmChanges).length) changes.frontmatter = fmChanges;

    if (Object.keys(changes).length === 0) {
      isEditing = false;
      isSaving = false;
      return;
    }

    try {
      await updateNode(node.id, changes);
      isEditing = false;
      onNodeClick(node.id); // refresh the panel
    } catch (err) {
      console.error('Failed to save:', err);
    } finally {
      isSaving = false;
    }
  }

  const EDGE_LABELS = {
    relates_to: 'Related',
    blocked_by: 'Blockers',
    supported_by: 'Supports',
    contradicts: 'Contradicts',
    inspired_by: 'Inspired By',
    involves: 'People',
    part_of: 'Part Of',
    located_in: 'Located In',
    funded_by: 'Funded By',
    met_at: 'Met At',
  };

  function getMetaEntries(node) {
    return Object.entries(node).filter(
      ([key, val]) => !SKIP_KEYS.has(key) && val != null && val !== ''
    );
  }

  function getDomain(type) {
    if (!schema?.types?.[type]) return null;
    return schema.types[type].domain || null;
  }

  function getGroupedNeighbors() {
    const groups = {};
    for (const n of neighbors) {
      const edgeType = n._edge_type || 'relates_to';
      if (!groups[edgeType]) {
        groups[edgeType] = {
          label: EDGE_LABELS[edgeType] || edgeType,
          nodes: [],
        };
      }
      groups[edgeType].nodes.push(n);
    }
    return Object.values(groups);
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
      {#if isEditing}
        <input class="edit-title" bind:value={editTitle} />
      {:else}
        <h2>{node.title || node.id}</h2>
      {/if}
    </div>
    <div class="header-actions">
      {#if isEditing}
        <button class="action-btn save" onclick={saveEdits} disabled={isSaving}>
          {isSaving ? 'Saving...' : 'Save'}
        </button>
        <button class="action-btn cancel" onclick={cancelEditing} disabled={isSaving}>Cancel</button>
      {:else}
        <button class="action-btn edit" onclick={startEditing}>Edit</button>
      {/if}
      <button class="close-btn" onclick={onClose}>&times;</button>
    </div>
  </div>

  {#if isEditing}
    <div class="tags">
      {#each editTags as tag}
        <span class="tag editable">
          {tag}
          <button class="tag-remove" onclick={() => removeTag(tag)}>&times;</button>
        </span>
      {/each}
      <input
        class="tag-input"
        bind:value={newTag}
        placeholder="+ tag"
        onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }}
      />
    </div>
  {:else if node.tags?.length}
    <div class="tags">
      {#each (Array.isArray(node.tags) ? node.tags : [node.tags]) as tag}
        <span class="tag">{tag}</span>
      {/each}
    </div>
  {/if}

  {#if isEditing}
    <div class="metadata">
      {#each Object.entries(editMeta) as [key, value]}
        <div class="meta-row">
          <span class="meta-key">{key}</span>
          <input class="meta-input" value={value} oninput={(e) => editMeta[key] = e.target.value} />
        </div>
      {/each}
    </div>
  {:else}
    <div class="metadata">
      {#each getMetaEntries(node) as [key, value]}
        <div class="meta-row">
          <span class="meta-key">{key}</span>
          <span class="meta-value">{typeof value === 'object' ? JSON.stringify(value) : value}</span>
        </div>
      {/each}
    </div>
  {/if}

  {#if isEditing}
    <textarea class="edit-content" bind:value={editContent} rows="10"></textarea>
  {:else if node.content}
    <div class="content">
      {@html formatText(node.content)}
    </div>
  {/if}

  {#if neighbors.length > 0}
    <div class="neighbors-section">
      <h3>Connections</h3>
      {#each getGroupedNeighbors() as group}
        <div class="edge-group">
          <span class="edge-type-label">{group.label}</span>
          {#each group.nodes as neighbor}
            <button class="neighbor-link" onclick={() => onNodeClick(neighbor.id)}>
              <span class="type-dot" style="background: {getTypeColor(neighbor.type)}"></span>
              <span class="neighbor-name">{neighbor.title || neighbor.id}</span>
              <span class="neighbor-type">{neighbor.type}</span>
              {#if neighbor._edge_direction === 'incoming'}
                <span class="direction-badge">in</span>
              {/if}
            </button>
          {/each}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .node-detail {
    position: absolute; top: 0; right: 0; width: 380px; height: 100%;
    background: var(--bg-surface); border-left: 1px solid var(--border);
    overflow-y: auto; padding: var(--space-lg); display: flex; flex-direction: column;
    gap: var(--space-md); z-index: 10;
    animation: slide-in 0.2s ease;
    box-shadow: var(--shadow-lg);
  }

  @keyframes slide-in {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }

  .panel-header {
    display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-sm);
  }

  .header-left { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; }

  .header-actions { display: flex; gap: 6px; flex-shrink: 0; }

  .domain-badge {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .type-badge {
    display: inline-block; font-size: var(--text-xs); font-weight: 600;
    padding: 2px 10px; border-radius: 10px; color: #0f0f1a;
    text-transform: uppercase; width: fit-content;
  }

  .panel-header h2 {
    font-size: var(--text-xl); font-weight: 600; word-wrap: break-word;
  }

  .close-btn {
    width: 28px; height: 28px; border: 1px solid var(--border); border-radius: 6px;
    background: transparent; color: var(--text-secondary); font-size: 16px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; transition: all var(--transition-fast);
  }
  .close-btn:hover { background: var(--bg-surface-hover); color: var(--text-primary); }

  .tags { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag {
    font-size: var(--text-xs); padding: 2px 8px; border-radius: 10px;
    background: var(--bg-primary); color: var(--text-secondary);
  }

  /* ── Metadata grid ── */

  .metadata {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 4px var(--space-md);
    font-size: var(--text-sm);
  }

  .meta-row {
    display: contents;
  }

  .meta-key {
    color: var(--text-muted);
    text-transform: capitalize;
  }

  .meta-value {
    color: var(--text-primary);
    font-weight: 500;
    text-align: right;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* ── Content ── */

  .content {
    font-size: var(--text-sm); line-height: 1.7; color: var(--text-secondary);
    padding: var(--space-md);
    background: var(--bg-primary); border-radius: var(--radius);
    overflow-wrap: break-word;
    word-break: break-word;
  }

  .content :global(p) { margin-bottom: 0.4em; }
  .content :global(p:last-child) { margin-bottom: 0; }
  .content :global(strong) { color: var(--text-primary); }
  .content :global(.fmt-inline-code) {
    background: var(--bg-surface); padding: 1px 4px; border-radius: 3px;
    font-family: var(--font-mono); font-size: 0.88em; color: var(--accent);
  }
  .content :global(.fmt-list) { padding-left: 1.2em; margin: 0.3em 0; }
  .content :global(.fmt-wikilink) { color: var(--accent); font-weight: 500; }

  /* ── Neighbors ── */

  .neighbors-section h3 {
    font-size: var(--text-xs); color: var(--text-muted); text-transform: uppercase;
    letter-spacing: 0.5px; margin-bottom: var(--space-sm);
  }

  .edge-group {
    margin-bottom: var(--space-sm);
  }

  .edge-type-label {
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--text-muted);
    padding: 2px 0;
    display: block;
    margin-bottom: 2px;
  }

  .neighbor-link {
    display: flex; align-items: center; gap: 6px; width: 100%;
    padding: 4px var(--space-sm); border: none; border-radius: 6px; background: transparent;
    color: var(--text-primary); font-size: var(--text-sm); text-align: left;
    transition: background var(--transition-fast); min-width: 0;
  }
  .neighbor-link:hover { background: var(--bg-surface-hover); }

  .type-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

  .neighbor-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .neighbor-type { font-size: var(--text-xs); color: var(--text-muted); }

  .direction-badge {
    font-size: 9px;
    padding: 1px 5px;
    border-radius: 3px;
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 600;
    text-transform: uppercase;
  }

  /* ── Edit mode ── */

  .action-btn {
    padding: 4px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    font-size: var(--text-xs);
    font-weight: 600;
    cursor: pointer;
    transition: all var(--transition-fast);
  }

  .action-btn.edit { color: var(--text-secondary); }
  .action-btn.edit:hover { background: var(--bg-surface-hover); color: var(--text-primary); }

  .action-btn.save {
    background: var(--accent);
    color: white;
    border-color: var(--accent);
  }
  .action-btn.save:hover { opacity: 0.9; }
  .action-btn.save:disabled { opacity: 0.5; cursor: not-allowed; }

  .action-btn.cancel { color: var(--text-muted); }
  .action-btn.cancel:hover { background: var(--bg-surface-hover); }

  .edit-title {
    font-size: var(--text-xl);
    font-weight: 600;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 6px 10px;
    color: var(--text-primary);
    outline: none;
    width: 100%;
  }
  .edit-title:focus { border-color: var(--accent); }

  .tag.editable {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .tag-remove {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 14px;
    cursor: pointer;
    padding: 0;
    line-height: 1;
  }
  .tag-remove:hover { color: var(--error); }

  .tag-input {
    font-size: var(--text-xs);
    padding: 2px 8px;
    border: 1px dashed var(--border);
    border-radius: 10px;
    background: transparent;
    color: var(--text-secondary);
    outline: none;
    width: 80px;
  }
  .tag-input:focus { border-color: var(--accent); }

  .meta-input {
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px 6px;
    color: var(--text-primary);
    font-size: var(--text-sm);
    font-weight: 500;
    text-align: right;
    width: 100%;
    outline: none;
  }
  .meta-input:focus { border-color: var(--accent); }

  .edit-content {
    font-size: var(--text-sm);
    line-height: 1.7;
    color: var(--text-secondary);
    padding: var(--space-md);
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    resize: vertical;
    outline: none;
    min-height: 150px;
    font-family: inherit;
    width: 100%;
    box-sizing: border-box;
  }
  .edit-content:focus { border-color: var(--accent); }
</style>
