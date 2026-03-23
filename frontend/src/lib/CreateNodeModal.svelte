<script>
  import { writeNode } from './api.js';
  import { getTypeColor, getDomainColor } from './colors.js';

  let { schema = null, onClose = () => {}, onCreated = () => {} } = $props();

  let title = $state('');
  let selectedType = $state('');
  let tagsInput = $state('');
  let content = $state('');
  let isSaving = $state(false);
  let errorMsg = $state('');
  let inputEl = $state(null);

  $effect(() => {
    if (inputEl) inputEl.focus();
  });

  // Auto-generate node_id from title (mirrors _sanitize_id())
  let nodeId = $derived(
    title.toLowerCase()
      .replace(/\s+/g, '-')
      .replace(/[^a-z0-9-]/g, '')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
  );

  let selectedDomain = $derived(
    selectedType && schema?.types?.[selectedType]?.domain || ''
  );

  function getTypeList() {
    return schema?.type_list || [];
  }

  function handleBackdropClick(e) {
    if (e.target === e.currentTarget) onClose();
  }

  function handleKeydown(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    errorMsg = '';

    if (!title.trim()) {
      errorMsg = 'Title is required';
      return;
    }
    if (!selectedType) {
      errorMsg = 'Type is required';
      return;
    }
    if (!nodeId) {
      errorMsg = 'Could not generate a valid ID from title';
      return;
    }

    const tags = tagsInput
      .split(',')
      .map(t => t.trim())
      .filter(t => t.length > 0);

    const today = new Date().toISOString().split('T')[0];

    isSaving = true;
    try {
      const result = await writeNode({
        node_id: nodeId,
        title: title.trim(),
        type: selectedType,
        content: content.trim(),
        frontmatter: {
          id: nodeId,
          type: selectedType,
          title: title.trim(),
          created: today,
          updated: today,
          tags,
        },
        edges: [],
      });
      onCreated(result.node_id || nodeId);
      onClose();
    } catch (err) {
      errorMsg = err.message || 'Failed to create node';
    } finally {
      isSaving = false;
    }
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="modal-backdrop" onclick={handleBackdropClick} onkeydown={handleKeydown}>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="modal" onclick={(e) => e.stopPropagation()}>
    <div class="modal-header">
      <h3>Create Node</h3>
      <button class="close-btn" onclick={onClose}>&times;</button>
    </div>

    <form class="modal-form" onsubmit={handleSubmit}>
      <div class="field">
        <label for="node-title">Title <span class="required">*</span></label>
        <input
          id="node-title"
          bind:this={inputEl}
          bind:value={title}
          placeholder="Node title..."
          autocomplete="off"
          spellcheck="false"
        />
        {#if nodeId}
          <span class="id-preview">ID: {nodeId}</span>
        {/if}
      </div>

      <div class="field">
        <label for="node-type">Type <span class="required">*</span></label>
        <div class="type-select-wrap">
          <select id="node-type" bind:value={selectedType}>
            <option value="" disabled>Select type...</option>
            {#each getTypeList() as type}
              <option value={type}>{type}</option>
            {/each}
          </select>
          {#if selectedDomain}
            <span class="domain-badge" style="color: {getDomainColor(selectedDomain)}">
              {selectedDomain}
            </span>
          {/if}
        </div>
      </div>

      <div class="field">
        <label for="node-tags">Tags <span class="optional">(comma-separated)</span></label>
        <input
          id="node-tags"
          bind:value={tagsInput}
          placeholder="tag1, tag2, tag3"
          autocomplete="off"
        />
      </div>

      <div class="field">
        <label for="node-content">Content</label>
        <textarea
          id="node-content"
          bind:value={content}
          placeholder="Node content..."
          rows="5"
        ></textarea>
      </div>

      {#if errorMsg}
        <div class="error-msg">{errorMsg}</div>
      {/if}

      <div class="modal-actions">
        <button type="button" class="btn-cancel" onclick={onClose} disabled={isSaving}>
          Cancel
        </button>
        <button type="submit" class="btn-create" disabled={isSaving || !title.trim() || !selectedType}>
          {isSaving ? 'Creating...' : 'Create'}
        </button>
      </div>
    </form>
  </div>
</div>

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(4px);
    z-index: 1000;
    display: flex;
    justify-content: center;
    padding-top: 12vh;
  }

  .modal {
    width: 480px;
    max-height: 80vh;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    align-self: flex-start;
  }

  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
  }

  .modal-header h3 {
    font-size: var(--text-base);
    font-weight: 600;
    color: var(--text-primary);
  }

  .close-btn {
    width: 28px; height: 28px;
    border: 1px solid var(--border); border-radius: 6px;
    background: transparent; color: var(--text-secondary); font-size: 16px;
    display: flex; align-items: center; justify-content: center;
    transition: all var(--transition-fast);
  }
  .close-btn:hover { background: var(--bg-surface-hover); color: var(--text-primary); }

  .modal-form {
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
    padding: 20px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  label {
    font-size: var(--text-sm);
    font-weight: 500;
    color: var(--text-secondary);
  }

  .required { color: var(--error); }
  .optional { color: var(--text-muted); font-weight: 400; font-size: var(--text-xs); }

  input, select, textarea {
    padding: 9px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--bg-primary);
    color: var(--text-primary);
    font-size: var(--text-sm);
    font-family: inherit;
    outline: none;
    transition: border-color var(--transition-fast);
  }

  input:focus, select:focus, textarea:focus {
    border-color: var(--accent);
  }

  input::placeholder, textarea::placeholder { color: var(--text-muted); }

  select {
    width: 100%;
    cursor: pointer;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2364748b' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 10px center;
    padding-right: 30px;
  }

  textarea {
    resize: vertical;
    min-height: 100px;
    line-height: 1.6;
  }

  .id-preview {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-family: var(--font-mono);
    padding: 2px 0;
  }

  .type-select-wrap {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }

  .type-select-wrap select { flex: 1; }

  .domain-badge {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .error-msg {
    font-size: var(--text-sm);
    color: var(--error);
    background: var(--error-soft);
    border: 1px solid rgba(248, 113, 113, 0.2);
    border-radius: var(--radius);
    padding: 8px 12px;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-sm);
    padding-top: var(--space-sm);
  }

  .btn-cancel {
    padding: 8px 16px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
    font-size: var(--text-sm);
    cursor: pointer;
    transition: all var(--transition-fast);
  }
  .btn-cancel:hover:not(:disabled) { background: var(--bg-surface-hover); }
  .btn-cancel:disabled { opacity: 0.5; cursor: not-allowed; }

  .btn-create {
    padding: 8px 20px;
    border: none;
    border-radius: 6px;
    background: var(--accent);
    color: white;
    font-size: var(--text-sm);
    font-weight: 600;
    cursor: pointer;
    transition: all var(--transition-fast);
  }
  .btn-create:hover:not(:disabled) { opacity: 0.9; }
  .btn-create:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
