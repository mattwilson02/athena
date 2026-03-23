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

  function fundamentalLabel(key) {
    const labels = {
      movement: 'Movement',
      sleep: 'Sleep',
      nutrition: 'Nutrition',
      connection: 'Connection',
      purpose: 'Purpose',
      financial_stability: 'Financial Stability',
    };
    return labels[key] ?? key;
  }

  function fundamentalStatusLabel(status) {
    if (status === 'active') return 'Active';
    if (status === 'neglected') return 'Neglected';
    return 'Untracked';
  }

  function stalenessLabel(staleness) {
    if (staleness === 'active') return 'Active';
    if (staleness === 'inactive') return 'Inactive';
    return 'Stale';
  }

  function relationshipLabel(rel) {
    if (!rel) return '';
    return rel.charAt(0).toUpperCase() + rel.slice(1);
  }

  function socialPatternLabel(pattern) {
    if (pattern === 'isolating') return 'Isolation Detected';
    if (pattern === 'overcommitting') return 'Overcommitting';
    if (pattern === 'no_data') return 'No Data';
    return 'Healthy';
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

    <!-- Fundamentals section -->
    {#if data.fundamentals && data.fundamentals.length > 0}
      <section class="section">
        <div class="section-header">
          <h3 class="section-title">Fundamentals</h3>
          {#if data.fundamentals_summary}
            <div class="fundamentals-summary">
              <span class="fund-stat active">{data.fundamentals_summary.active} active</span>
              <span class="fund-stat neglected">{data.fundamentals_summary.neglected} neglected</span>
              <span class="fund-stat no-data">{data.fundamentals_summary.no_data} untracked</span>
            </div>
          {/if}
        </div>
        <div class="fundamentals-grid">
          {#each data.fundamentals as fund}
            <div class="fundamental-card" class:neglected={fund.status === 'neglected'} class:no-data={fund.status === 'no_data'} class:active={fund.status === 'active'}>
              <div class="fundamental-header">
                <span class="fundamental-name">{fundamentalLabel(fund.fundamental)}</span>
                <span class="fundamental-badge {fund.status}">{fundamentalStatusLabel(fund.status)}</span>
              </div>
              {#if fund.status !== 'no_data'}
                <div class="fundamental-meta">
                  {#if fund.days_since_activity != null}
                    <span class="fundamental-recency">Last: {formatDaysAgo(fund.days_since_activity)}</span>
                  {/if}
                  {#if fund.related_habits && fund.related_habits.length > 0}
                    <span class="fundamental-habits">{fund.related_habits.join(', ')}</span>
                  {/if}
                </div>
              {/if}
              {#if fund.message}
                <div class="fundamental-message">{fund.message}</div>
              {/if}
            </div>
          {/each}
        </div>
      </section>
    {/if}

    <!-- Relationships section -->
    {#if data.relationships}
      <section class="section">
        <div class="section-header">
          <h3 class="section-title">Relationships</h3>
          {#if data.relationships.summary}
            <div class="rel-summary">
              <span class="rel-stat active">{data.relationships.summary.active} active</span>
              <span class="rel-stat stale">{data.relationships.summary.stale} stale</span>
              {#if data.relationships.summary.inactive > 0}
                <span class="rel-stat inactive">{data.relationships.summary.inactive} inactive</span>
              {/if}
            </div>
          {/if}
        </div>

        <!-- Social pattern banner -->
        {#if data.relationships.social_pattern && data.relationships.social_pattern.pattern !== 'healthy' && data.relationships.social_pattern.pattern !== 'no_data'}
          <div class="social-pattern-banner" class:isolating={data.relationships.social_pattern.pattern === 'isolating'} class:overcommitting={data.relationships.social_pattern.pattern === 'overcommitting'}>
            <div class="social-pattern-header">
              <span class="social-pattern-label">{socialPatternLabel(data.relationships.social_pattern.pattern)}</span>
              <span class="social-pattern-confidence">{data.relationships.social_pattern.confidence} confidence</span>
            </div>
            {#if data.relationships.social_pattern.signals && data.relationships.social_pattern.signals.length > 0}
              <div class="social-pattern-signals">
                {#each data.relationships.social_pattern.signals as signal}
                  <span class="social-signal">{signal.detail}</span>
                {/each}
              </div>
            {/if}
          </div>
        {/if}

        {#if data.relationships.persons && data.relationships.persons.length === 0}
          <div class="empty-state">No person nodes in graph yet.</div>
        {:else if data.relationships.persons}
          <div class="person-list">
            {#each data.relationships.persons as person}
              <div class="person-card" class:person-active={person.staleness === 'active'} class:person-stale={person.staleness === 'stale'} class:person-inactive={person.staleness === 'inactive'}>
                <div class="person-header">
                  <div class="person-name-row">
                    <span class="person-name">{person.person_title}</span>
                    {#if person.relationship}
                      <span class="person-rel-type">{relationshipLabel(person.relationship)}</span>
                    {/if}
                  </div>
                  <span class="person-staleness-badge {person.staleness}">{stalenessLabel(person.staleness)}</span>
                </div>
                <div class="person-meta">
                  <span class="person-last-updated">
                    {#if person.days_since_update != null}
                      Last updated: {formatDaysAgo(person.days_since_update)}
                    {:else}
                      Never updated
                    {/if}
                  </span>
                  <span class="person-connections">{person.connected_node_count} connections</span>
                </div>
                {#if person.connection_types && person.connection_types.length > 0}
                  <div class="person-connection-types">
                    {#each person.connection_types as type}
                      <span class="connection-type-chip">{type}</span>
                    {/each}
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      </section>
    {/if}
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

  /* Fundamentals */
  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .fundamentals-summary {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }

  .fund-stat {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
  }

  .fund-stat.active {
    background: var(--success-soft);
    color: var(--success);
  }

  .fund-stat.neglected {
    background: var(--error-soft);
    color: var(--error);
  }

  .fund-stat.no-data {
    background: var(--bg-surface-hover);
    color: var(--text-muted);
  }

  .fundamentals-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: var(--space-sm);
  }

  .fundamental-card {
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-xs);
  }

  .fundamental-card.neglected { border-left: 3px solid var(--error); }
  .fundamental-card.no-data { border-left: 3px solid var(--border); opacity: 0.7; }
  .fundamental-card.active { border-left: 3px solid var(--success); }

  .fundamental-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .fundamental-name {
    font-size: var(--text-sm);
    font-weight: 500;
    color: var(--text-primary);
  }

  .fundamental-badge {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 99px;
    flex-shrink: 0;
  }

  .fundamental-badge.active {
    background: var(--success-soft);
    color: var(--success);
  }

  .fundamental-badge.neglected {
    background: var(--error-soft);
    color: var(--error);
  }

  .fundamental-badge.no_data {
    background: var(--bg-surface-hover);
    color: var(--text-muted);
  }

  .fundamental-meta {
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .fundamental-recency {
    font-weight: 500;
    color: var(--text-secondary);
  }

  .fundamental-habits {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .fundamental-message {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-style: italic;
    line-height: 1.4;
  }

  /* Relationships */
  .rel-summary {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
  }

  .rel-stat {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
  }

  .rel-stat.active {
    background: var(--success-soft);
    color: var(--success);
  }

  .rel-stat.stale {
    background: var(--warning-soft);
    color: var(--warning);
  }

  .rel-stat.inactive {
    background: var(--bg-surface-hover);
    color: var(--text-muted);
  }

  /* Social pattern banner */
  .social-pattern-banner {
    padding: var(--space-sm) var(--space-md);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-xs);
  }

  .social-pattern-banner.isolating {
    background: var(--error-soft);
    border-color: var(--error);
  }

  .social-pattern-banner.overcommitting {
    background: var(--warning-soft);
    border-color: var(--warning);
  }

  .social-pattern-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .social-pattern-label {
    font-size: var(--text-sm);
    font-weight: 600;
  }

  .isolating .social-pattern-label { color: var(--error); }
  .overcommitting .social-pattern-label { color: var(--warning); }

  .social-pattern-confidence {
    font-size: var(--text-xs);
    color: var(--text-muted);
    text-transform: capitalize;
  }

  .social-pattern-signals {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .social-signal {
    font-size: var(--text-xs);
    color: var(--text-secondary);
    line-height: 1.4;
  }

  /* Person list */
  .person-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .person-card {
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-xs);
  }

  .person-card.person-active { border-left: 3px solid var(--success); }
  .person-card.person-stale { border-left: 3px solid var(--warning); }
  .person-card.person-inactive { border-left: 3px solid var(--border); opacity: 0.6; }

  .person-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .person-name-row {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    flex: 1;
    overflow: hidden;
  }

  .person-name {
    font-size: var(--text-base);
    font-weight: 500;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .person-rel-type {
    font-size: var(--text-xs);
    color: #60a5fa;
    background: rgba(96, 165, 250, 0.1);
    padding: 1px 6px;
    border-radius: 4px;
    flex-shrink: 0;
  }

  .person-staleness-badge {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
    flex-shrink: 0;
  }

  .person-staleness-badge.active {
    background: var(--success-soft);
    color: var(--success);
  }

  .person-staleness-badge.stale {
    background: var(--warning-soft);
    color: var(--warning);
  }

  .person-staleness-badge.inactive {
    background: var(--bg-surface-hover);
    color: var(--text-muted);
  }

  .person-meta {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .person-last-updated {
    font-weight: 500;
    color: var(--text-secondary);
  }

  .person-connections {
    color: var(--text-muted);
  }

  .person-connection-types {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-xs);
  }

  .connection-type-chip {
    font-size: 10px;
    padding: 1px 6px;
    background: var(--bg-surface-hover);
    border-radius: 4px;
    color: var(--text-muted);
    text-transform: capitalize;
  }
</style>
