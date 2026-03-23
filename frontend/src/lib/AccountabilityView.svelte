<script>
  import { getAccountability } from './api.js';

  let data = $state(null);
  let isLoading = $state(true);
  let error = $state(null);

  $effect(() => {
    isLoading = true;
    error = null;
    getAccountability()
      .then(d => { data = d; })
      .catch(e => { error = e.message || 'Failed to load accountability data'; })
      .finally(() => { isLoading = false; });
  });

  function streakStatusLabel(status) {
    if (status === 'on_track') return 'On Track';
    if (status === 'at_risk') return 'At Risk';
    return 'Broken';
  }

  function formatDaysAgo(days) {
    if (days == null) return '—';
    if (days === 0) return 'today';
    if (days === 1) return 'yesterday';
    return `${days}d ago`;
  }

  function formatDaysOverdue(days) {
    if (days === 1) return '1 day overdue';
    return `${days} days overdue`;
  }

  function formatDate(dateStr) {
    if (!dateStr) return '—';
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
</script>

<div class="accountability-view">
  <div class="view-header">
    <h2>Accountability</h2>
  </div>

  {#if isLoading}
    <div class="loading">Loading...</div>
  {:else if error}
    <div class="error-state">{error}</div>
  {:else if data}
    <!-- Summary bar -->
    <div class="summary-bar">
      <div class="summary-stat">
        <span class="summary-value">{data.summary.total_habits}</span>
        <span class="summary-label">Habits</span>
      </div>
      <div class="summary-stat on-track">
        <span class="summary-value">{data.summary.on_track}</span>
        <span class="summary-label">On Track</span>
      </div>
      <div class="summary-stat at-risk">
        <span class="summary-value">{data.summary.at_risk}</span>
        <span class="summary-label">At Risk</span>
      </div>
      <div class="summary-stat broken">
        <span class="summary-value">{data.summary.broken}</span>
        <span class="summary-label">Broken</span>
      </div>
      <div class="summary-stat overdue">
        <span class="summary-value">{data.summary.overdue_count}</span>
        <span class="summary-label">Overdue</span>
      </div>
    </div>

    <!-- Streaks section -->
    <section class="section">
      <h3 class="section-title">Habit Streaks</h3>
      {#if data.streaks.length === 0}
        <div class="empty-state">No active habits tracked yet.</div>
      {:else}
        <div class="streak-list">
          {#each data.streaks as streak}
            <div class="streak-card" class:broken={streak.streak_status === 'broken'} class:at-risk={streak.streak_status === 'at_risk'} class:on-track={streak.streak_status === 'on_track'}>
              <div class="streak-header">
                <span class="streak-title">{streak.habit_title}</span>
                <span class="streak-badge {streak.streak_status}">{streakStatusLabel(streak.streak_status)}</span>
              </div>
              <div class="streak-meta">
                <span class="streak-count">
                  {#if streak.current_streak > 0}
                    {streak.current_streak} streak
                  {:else}
                    No streak
                  {/if}
                </span>
                <span class="streak-freq">{streak.frequency}</span>
                <span class="streak-last">Last: {formatDaysAgo(streak.days_since_last)}</span>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </section>

    <!-- Overdue commitments section -->
    <section class="section">
      <h3 class="section-title">Overdue Commitments</h3>
      {#if data.overdue.length === 0}
        <div class="empty-state">No overdue commitments.</div>
      {:else}
        <div class="overdue-list">
          {#each data.overdue as item}
            <div class="overdue-card">
              <div class="overdue-header">
                <span class="overdue-title">{item.title}</span>
                <span class="overdue-badge">{formatDaysOverdue(item.days_overdue)}</span>
              </div>
              <div class="overdue-meta">
                <span class="overdue-type">{item.type}</span>
                {#if item.priority}
                  <span class="overdue-priority {item.priority}">{item.priority}</span>
                {/if}
                <span class="overdue-due">due {formatDate(item.due)}</span>
              </div>
              {#if item.committed_on}
                <div class="commitment-context">
                  <span class="committed-on">Committed {formatDate(item.committed_on)}</span>
                  {#if item.commitment_context}
                    <span class="committed-text">"{item.commitment_context}"</span>
                  {/if}
                </div>
              {/if}
              {#if item.consequences && item.consequences.length > 0}
                <div class="consequences">
                  <span class="consequences-label">Impacts:</span>
                  {#each item.consequences as consequence}
                    <span class="consequence-chip">
                      {consequence.title}
                      <span class="consequence-type">{consequence.type}</span>
                    </span>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .accountability-view {
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

  .summary-stat.on-track .summary-value { color: var(--success); }
  .summary-stat.at-risk .summary-value { color: var(--warning); }
  .summary-stat.broken .summary-value { color: var(--error); }
  .summary-stat.overdue .summary-value { color: var(--error); }

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

  /* Streak cards */
  .streak-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .streak-card {
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-xs);
  }

  .streak-card.broken { border-left: 3px solid var(--error); }
  .streak-card.at-risk { border-left: 3px solid var(--warning); }
  .streak-card.on-track { border-left: 3px solid var(--success); }

  .streak-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .streak-title {
    font-size: var(--text-base);
    font-weight: 500;
    color: var(--text-primary);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .streak-badge {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
    flex-shrink: 0;
  }

  .streak-badge.on_track {
    background: var(--success-soft);
    color: var(--success);
  }

  .streak-badge.at_risk {
    background: var(--warning-soft);
    color: var(--warning);
  }

  .streak-badge.broken {
    background: var(--error-soft);
    color: var(--error);
  }

  .streak-meta {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .streak-count {
    font-weight: 600;
    color: var(--text-secondary);
  }

  /* Overdue cards */
  .overdue-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .overdue-card {
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    border-left: 3px solid var(--error);
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .overdue-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .overdue-title {
    font-size: var(--text-base);
    font-weight: 500;
    color: var(--text-primary);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .overdue-badge {
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--error);
    background: var(--error-soft);
    padding: 2px 8px;
    border-radius: 99px;
    flex-shrink: 0;
  }

  .overdue-meta {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .overdue-type {
    background: var(--bg-surface-hover);
    padding: 1px 6px;
    border-radius: 4px;
    color: var(--text-secondary);
  }

  .overdue-priority {
    padding: 1px 6px;
    border-radius: 4px;
    font-weight: 600;
  }

  .overdue-priority.high { background: var(--error-soft); color: var(--error); }
  .overdue-priority.medium { background: var(--warning-soft); color: var(--warning); }
  .overdue-priority.low { background: var(--success-soft); color: var(--success); }

  .overdue-due { color: var(--text-muted); }

  .commitment-context {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: var(--text-xs);
  }

  .committed-on {
    color: var(--text-muted);
    font-weight: 500;
  }

  .committed-text {
    color: var(--text-secondary);
    font-style: italic;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .consequences {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-xs);
    font-size: var(--text-xs);
  }

  .consequences-label {
    color: var(--text-muted);
    font-weight: 500;
    flex-shrink: 0;
  }

  .consequence-chip {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    background: var(--bg-surface-hover);
    border-radius: 99px;
    color: var(--text-secondary);
  }

  .consequence-type {
    color: var(--text-muted);
    font-size: 10px;
  }
</style>
