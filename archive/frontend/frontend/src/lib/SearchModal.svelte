<script>
  import { searchNodes } from './api.js';
  import { getTypeColor } from './colors.js';

  let { onSelect = () => {}, onClose = () => {} } = $props();

  let query = $state('');
  let results = $state([]);
  let selectedIndex = $state(0);
  let isSearching = $state(false);
  let inputEl = $state(null);
  let debounceTimer = null;

  $effect(() => {
    if (inputEl) inputEl.focus();
  });

  function handleInput(e) {
    query = e.target.value;
    selectedIndex = 0;
    clearTimeout(debounceTimer);
    if (!query.trim()) {
      results = [];
      return;
    }
    debounceTimer = setTimeout(doSearch, 200);
  }

  async function doSearch() {
    if (!query.trim()) return;
    isSearching = true;
    try {
      const data = await searchNodes(query);
      results = data.results || [];
    } catch {
      results = [];
    } finally {
      isSearching = false;
    }
  }

  function handleKeydown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = Math.min(selectedIndex + 1, results.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = Math.max(selectedIndex - 1, 0);
    } else if (e.key === 'Enter' && results.length > 0) {
      e.preventDefault();
      selectResult(results[selectedIndex]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  }

  function selectResult(result) {
    onSelect(result.id);
    onClose();
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="modal-backdrop" onclick={onClose} onkeydown={handleKeydown}>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="modal" onclick={(e) => e.stopPropagation()}>
    <div class="search-input-wrap">
      <svg class="search-icon" viewBox="0 0 20 20" fill="currentColor" width="18" height="18">
        <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd"/>
      </svg>
      <input
        bind:this={inputEl}
        value={query}
        oninput={handleInput}
        onkeydown={handleKeydown}
        placeholder="Search nodes..."
        spellcheck="false"
        autocomplete="off"
      />
      <kbd class="esc-hint">esc</kbd>
    </div>

    {#if results.length > 0}
      <div class="results">
        {#each results as result, i}
          <button
            class="result-row"
            class:selected={i === selectedIndex}
            onclick={() => selectResult(result)}
            onmouseenter={() => selectedIndex = i}
          >
            <span class="type-dot" style="background: {getTypeColor(result.type)}"></span>
            <span class="result-title">{result.title || result.id}</span>
            <span class="result-type" style="color: {getTypeColor(result.type)}">{result.type}</span>
            {#if result.match_type === 'title'}
              <span class="exact-badge">exact</span>
            {/if}
          </button>
        {/each}
      </div>
    {:else if query.trim() && !isSearching}
      <div class="no-results">No results for "{query}"</div>
    {/if}
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
    padding-top: 15vh;
  }

  .modal {
    width: 560px;
    max-height: 420px;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    align-self: flex-start;
  }

  .search-input-wrap {
    display: flex;
    align-items: center;
    padding: 14px 16px;
    gap: 10px;
    border-bottom: 1px solid var(--border);
  }

  .search-icon {
    color: var(--text-muted);
    flex-shrink: 0;
  }

  input {
    flex: 1;
    background: none;
    border: none;
    outline: none;
    color: var(--text-primary);
    font-size: var(--text-base);
  }

  input::placeholder { color: var(--text-muted); }

  .esc-hint {
    font-size: 11px;
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-muted);
    background: var(--bg-primary);
  }

  .results {
    overflow-y: auto;
    padding: 6px;
  }

  .result-row {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px 12px;
    border: none;
    background: none;
    color: var(--text-primary);
    border-radius: var(--radius);
    cursor: pointer;
    text-align: left;
    font-size: var(--text-sm);
    transition: background var(--transition-fast);
  }

  .result-row:hover,
  .result-row.selected {
    background: var(--bg-surface-hover);
  }

  .type-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .result-title {
    flex: 1;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .result-type {
    font-size: var(--text-xs);
    opacity: 0.7;
  }

  .exact-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .no-results {
    padding: 20px;
    text-align: center;
    color: var(--text-muted);
    font-size: var(--text-sm);
  }
</style>
