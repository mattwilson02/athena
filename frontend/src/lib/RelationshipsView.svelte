<script>
  import { getRelationships } from './api.js';

  let data = $state(null);
  let isLoading = $state(true);
  let error = $state(null);

  $effect(() => {
    isLoading = true;
    error = null;
    getRelationships()
      .then(d => { data = d; })
      .catch(e => { error = e.message || 'Failed to load relationship data'; })
      .finally(() => { isLoading = false; });
  });

  function healthLabel(health) {
    if (health === 'active') return 'Active';
    if (health === 'drifting') return 'Drifting';
    if (health === 'neglected') return 'Neglected';
    return 'Untracked';
  }

  function contextProfileLabel(profile) {
    if (profile === 'mostly_positive') return 'Mostly positive';
    if (profile === 'mostly_negative') return 'Mostly negative';
    if (profile === 'mixed') return 'Mixed';
    return 'Neutral';
  }

  function formatDaysAgo(days) {
    if (days == null) return 'never';
    if (days === 0) return 'today';
    if (days === 1) return 'yesterday';
    return `${days}d ago`;
  }

  function formatLastMentioned(dateStr) {
    if (!dateStr) return null;
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function influenceBarWidth(score) {
    return `${Math.round((score || 0) * 100)}%`;
  }
</script>

<div class="relationships-view">
  <div class="view-header">
    <h2>Relationships</h2>
  </div>

  {#if isLoading}
    <div class="loading">Loading...</div>
  {:else if error}
    <div class="error-state">{error}</div>
  {:else if data}
    <!-- Summary bar -->
    <div class="summary-bar">
      <div class="summary-stat">
        <span class="summary-value">{data.summary.total_persons}</span>
        <span class="summary-label">People</span>
      </div>
      <div class="summary-stat active">
        <span class="summary-value">{data.summary.active}</span>
        <span class="summary-label">Active</span>
      </div>
      <div class="summary-stat drifting">
        <span class="summary-value">{data.summary.drifting}</span>
        <span class="summary-label">Drifting</span>
      </div>
      <div class="summary-stat neglected">
        <span class="summary-value">{data.summary.neglected}</span>
        <span class="summary-label">Neglected</span>
      </div>
      <div class="summary-stat no-data">
        <span class="summary-value">{data.summary.no_data}</span>
        <span class="summary-label">Untracked</span>
      </div>
    </div>

    <!-- Relationships list -->
    <section class="section">
      <h3 class="section-title">All Relationships</h3>
      {#if data.relationships.length === 0}
        <div class="empty-state">No person nodes in the graph yet.</div>
      {:else}
        <div class="rel-list">
          {#each data.relationships as rel}
            <div
              class="rel-card"
              class:active={rel.health === 'active'}
              class:drifting={rel.health === 'drifting'}
              class:neglected={rel.health === 'neglected'}
              class:no-data={rel.health === 'no_data'}
            >
              <div class="rel-header">
                <div class="rel-name-group">
                  <span class="rel-name">{rel.person_title}</span>
                  {#if rel.relationship}
                    <span class="rel-type">{rel.relationship}</span>
                  {/if}
                </div>
                <span class="rel-badge {rel.health}">{healthLabel(rel.health)}</span>
              </div>

              <div class="rel-meta">
                {#if rel.expected_frequency}
                  <span class="rel-freq">Expected: {rel.expected_frequency}</span>
                {/if}
                <span class="rel-last">
                  Last: {formatDaysAgo(rel.days_since_mention)}
                  {#if rel.last_mentioned}
                    ({formatLastMentioned(rel.last_mentioned)})
                  {/if}
                </span>
                {#if rel.drift_days > 0}
                  <span class="rel-drift">{rel.drift_days}d past due</span>
                {/if}
              </div>

              {#if rel.mention_count > 0}
                <div class="rel-stats">
                  <div class="rel-influence">
                    <div class="influence-bar-bg">
                      <div class="influence-bar-fill" style="width: {influenceBarWidth(rel.influence_score)}"></div>
                    </div>
                    <span class="influence-label">{rel.mention_count} mention{rel.mention_count === 1 ? '' : 's'} · rank #{rel.influence_rank}</span>
                  </div>
                  <span class="rel-context-profile {rel.context_profile.replace('_', '-')}">{contextProfileLabel(rel.context_profile)}</span>
                </div>

                <div class="mention-contexts">
                  {#if rel.mention_contexts.positive > 0}
                    <span class="ctx-chip positive">{rel.mention_contexts.positive} positive</span>
                  {/if}
                  {#if rel.mention_contexts.negative > 0}
                    <span class="ctx-chip negative">{rel.mention_contexts.negative} negative</span>
                  {/if}
                  {#if rel.mention_contexts.planning > 0}
                    <span class="ctx-chip planning">{rel.mention_contexts.planning} planning</span>
                  {/if}
                  {#if rel.mention_contexts.neutral > 0}
                    <span class="ctx-chip neutral">{rel.mention_contexts.neutral} neutral</span>
                  {/if}
                </div>

                {#if rel.recent_topics && rel.recent_topics.length > 0}
                  <div class="recent-topics">
                    <span class="topics-label">Topics:</span>
                    {#each rel.recent_topics as topic}
                      <span class="topic-chip">{topic}</span>
                    {/each}
                  </div>
                {/if}
              {:else}
                <div class="rel-no-mentions">Not mentioned in the last 30 days</div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .relationships-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow-y: auto;
    padding: var(--space-lg);
    gap: var(--space-lg);
  }

  .view-header h2 {
    font-size: var(--text-xl);
    font-weight: 700;
  }

  .loading, .error-state, .empty-state {
    color: var(--text-muted);
    font-size: var(--text-sm);
    padding: var(--space-md) 0;
  }

  .error-state { color: var(--error); }

  /* Summary bar */
  .summary-bar {
    display: flex;
    gap: var(--space-md);
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius-lg);
    border: 1px solid var(--border);
  }

  .summary-stat {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
    gap: 2px;
  }

  .summary-stat + .summary-stat {
    border-left: 1px solid var(--border);
  }

  .summary-value {
    font-size: var(--text-xl);
    font-weight: 700;
    color: var(--text-primary);
  }

  .summary-label {
    font-size: var(--text-xs);
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }

  .summary-stat.active .summary-value { color: var(--success); }
  .summary-stat.drifting .summary-value { color: var(--warning); }
  .summary-stat.neglected .summary-value { color: var(--error); }

  /* Sections */
  .section {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .section-title {
    font-size: var(--text-sm);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
  }

  /* Relationship cards */
  .rel-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .rel-card {
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .rel-card.neglected { border-left: 3px solid var(--error); }
  .rel-card.drifting { border-left: 3px solid var(--warning); }
  .rel-card.active { border-left: 3px solid var(--success); }
  .rel-card.no-data { border-left: 3px solid var(--border); opacity: 0.7; }

  .rel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .rel-name-group {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex: 1;
    overflow: hidden;
  }

  .rel-name {
    font-size: var(--text-base);
    font-weight: 500;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .rel-type {
    font-size: var(--text-xs);
    color: var(--text-muted);
    background: var(--bg-surface-hover);
    padding: 1px 6px;
    border-radius: 4px;
    flex-shrink: 0;
    white-space: nowrap;
  }

  .rel-badge {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
    flex-shrink: 0;
  }

  .rel-badge.active {
    background: var(--success-soft);
    color: var(--success);
  }

  .rel-badge.drifting {
    background: var(--warning-soft);
    color: var(--warning);
  }

  .rel-badge.neglected {
    background: var(--error-soft);
    color: var(--error);
  }

  .rel-badge.no_data {
    background: var(--bg-surface-hover);
    color: var(--text-muted);
  }

  .rel-meta {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .rel-freq {
    font-weight: 500;
    color: var(--text-secondary);
  }

  .rel-last {
    color: var(--text-muted);
  }

  .rel-drift {
    color: var(--warning);
    font-weight: 600;
  }

  /* Influence stats */
  .rel-stats {
    display: flex;
    align-items: center;
    gap: var(--space-md);
  }

  .rel-influence {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex: 1;
  }

  .influence-bar-bg {
    width: 80px;
    height: 4px;
    background: var(--bg-surface-hover);
    border-radius: 2px;
    flex-shrink: 0;
  }

  .influence-bar-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    min-width: 2px;
  }

  .influence-label {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  /* Context profile */
  .rel-context-profile {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
    flex-shrink: 0;
  }

  .rel-context-profile.mostly-positive {
    background: var(--success-soft);
    color: var(--success);
  }

  .rel-context-profile.mostly-negative {
    background: var(--error-soft);
    color: var(--error);
  }

  .rel-context-profile.mixed {
    background: var(--warning-soft);
    color: var(--warning);
  }

  .rel-context-profile.neutral {
    background: var(--bg-surface-hover);
    color: var(--text-muted);
  }

  /* Context chips */
  .mention-contexts {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-xs);
  }

  .ctx-chip {
    font-size: var(--text-xs);
    padding: 1px 8px;
    border-radius: 99px;
  }

  .ctx-chip.positive {
    background: var(--success-soft);
    color: var(--success);
  }

  .ctx-chip.negative {
    background: var(--error-soft);
    color: var(--error);
  }

  .ctx-chip.planning {
    background: var(--accent-soft);
    color: var(--accent);
  }

  .ctx-chip.neutral {
    background: var(--bg-surface-hover);
    color: var(--text-muted);
  }

  /* Recent topics */
  .recent-topics {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-xs);
    font-size: var(--text-xs);
  }

  .topics-label {
    color: var(--text-muted);
    font-weight: 500;
    flex-shrink: 0;
  }

  .topic-chip {
    padding: 1px 8px;
    background: var(--bg-surface-hover);
    border-radius: 99px;
    color: var(--text-secondary);
  }

  .rel-no-mentions {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-style: italic;
  }
</style>
