<script>
  import { getBriefing } from './api.js';

  let { onNodeSelect = () => {} } = $props();

  let data = $state(null);
  let isLoading = $state(true);
  let error = $state(null);
  let yesterdayExpanded = $state(false);
  let patternsExpanded = $state(false);

  $effect(() => {
    isLoading = true;
    error = null;
    getBriefing()
      .then(d => { data = d; })
      .catch(e => { error = e.message || 'Failed to load daily briefing'; })
      .finally(() => { isLoading = false; });
  });

  function formatTime(timeStr) {
    if (!timeStr) return null;
    // Handle HH:MM format
    const match = timeStr.match(/^(\d{1,2}):(\d{2})$/);
    if (match) {
      const h = parseInt(match[1]);
      const m = match[2];
      const ampm = h >= 12 ? 'PM' : 'AM';
      const h12 = h % 12 || 12;
      return `${h12}:${m} ${ampm}`;
    }
    return timeStr;
  }

  function formatDate(dateStr) {
    if (!dateStr) return '—';
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function priorityLabel(p) {
    if (p === 'high') return 'High';
    if (p === 'medium') return 'Med';
    if (p === 'low') return 'Low';
    return p || '—';
  }

  function streakStatusLabel(status) {
    if (status === 'on_track') return 'On Track';
    if (status === 'at_risk') return 'At Risk';
    return 'Broken';
  }

  function completionRingStyle(rate) {
    const pct = Math.round((rate || 0) * 100);
    return pct;
  }

  function hasAnyContent(briefing) {
    if (!briefing) return false;
    return (
      briefing.events?.length > 0 ||
      briefing.due_tasks?.length > 0 ||
      briefing.habit_targets?.length > 0 ||
      briefing.active_plan != null ||
      briefing.overdue_summary?.count > 0
    );
  }
</script>

<div class="daily-plan-view">
  <div class="view-header">
    <div class="header-left">
      <h2>Today</h2>
      {#if data?.briefing}
        <span class="header-date">{data.briefing.day_of_week}, {formatDate(data.briefing.date)}</span>
      {/if}
    </div>
    {#if data?.briefing?.active_plan}
      <div class="completion-ring">
        <span class="completion-pct">{completionRingStyle(data.briefing.active_plan.completion_rate)}%</span>
        <span class="completion-label">planned</span>
      </div>
    {/if}
  </div>

  {#if isLoading}
    <div class="loading">Loading...</div>
  {:else if error}
    <div class="error-state">{error}</div>
  {:else if data}

    {#if !hasAnyContent(data.briefing)}
      <div class="empty-day">
        <p class="empty-msg">Nothing on the schedule yet.</p>
        <p class="empty-hint">Tell Athena what you're focusing on today to capture a plan.</p>
      </div>
    {:else}

      <!-- Schedule section -->
      {#if data.briefing.events?.length > 0}
        <section class="section">
          <h3 class="section-title">Scheduled</h3>
          <div class="event-list">
            {#each data.briefing.events as event}
              <div class="event-card">
                <div class="event-time">
                  {#if event.time}
                    <span class="time-label">{formatTime(event.time)}</span>
                  {:else}
                    <span class="time-label muted">All day</span>
                  {/if}
                </div>
                <div class="event-body">
                  <span class="event-title">{event.title}</span>
                  <div class="event-meta">
                    {#if event.location}
                      <span class="event-location">@ {event.location}</span>
                    {/if}
                    {#if event.people?.length > 0}
                      <span class="event-people">with {event.people.join(', ')}</span>
                    {/if}
                    <span class="event-status {event.status}">{event.status}</span>
                  </div>
                </div>
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <!-- Active plan section -->
      {#if data.briefing.active_plan}
        <section class="section">
          <div class="section-header">
            <h3 class="section-title">Plan</h3>
            <span class="plan-progress">
              {data.plan_reality?.completed_count ?? 0} / {data.briefing.active_plan.planned?.length ?? 0} done
            </span>
          </div>
          {#if data.briefing.active_plan.planned?.length > 0}
            <div class="plan-list">
              {#each data.briefing.active_plan.planned as item}
                <div class="plan-item" class:completed={item.completed}>
                  <span class="plan-check">{item.completed ? '✓' : '○'}</span>
                  <span class="plan-desc">{item.description}</span>
                  {#if item.linked_node}
                    <button
                      class="plan-node-link"
                      onclick={() => onNodeSelect(item.linked_node)}
                      title="View node"
                    >↗</button>
                  {/if}
                </div>
              {/each}
            </div>
          {:else}
            <div class="empty-state">Plan captured but no items found.</div>
          {/if}
        </section>
      {:else}
        <section class="section">
          <h3 class="section-title">Plan</h3>
          <div class="no-plan-prompt">
            <p>No plan captured yet.</p>
            <p class="no-plan-hint">Tell Athena what you're focusing on today to capture a plan.</p>
          </div>
        </section>
      {/if}

      <!-- Due tasks section -->
      {#if data.briefing.due_tasks?.length > 0}
        <section class="section">
          <h3 class="section-title">Due Today</h3>
          <div class="task-list">
            {#each data.briefing.due_tasks as task}
              <div class="task-card" class:overdue={task.days_overdue > 0}>
                <div class="task-header">
                  <span class="task-title">{task.title}</span>
                  <span class="priority-badge {task.priority}">{priorityLabel(task.priority)}</span>
                </div>
                <div class="task-meta">
                  {#if task.project}
                    <span class="task-project">{task.project}</span>
                  {/if}
                  {#if task.days_overdue > 0}
                    <span class="overdue-label">{task.days_overdue}d overdue</span>
                  {:else}
                    <span class="due-today-label">Due today</span>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </section>
      {/if}

      <!-- Habit targets section -->
      {#if data.briefing.habit_targets?.length > 0}
        <section class="section">
          <h3 class="section-title">Habits Due Today</h3>
          <div class="habit-list">
            {#each data.briefing.habit_targets as habit}
              {#if habit.due_today}
                <div class="habit-card" class:at-risk={habit.streak_status === 'at_risk'} class:broken={habit.streak_status === 'broken'} class:on-track={habit.streak_status === 'on_track'}>
                  <div class="habit-header">
                    <span class="habit-title">{habit.title}</span>
                    <span class="streak-badge {habit.streak_status}">{streakStatusLabel(habit.streak_status)}</span>
                  </div>
                  <div class="habit-meta">
                    <span class="habit-freq">{habit.frequency}</span>
                    {#if habit.current_streak > 0}
                      <span class="habit-streak">{habit.current_streak} streak</span>
                    {/if}
                    {#if habit.last_completed}
                      <span class="habit-last">Last: {formatDate(habit.last_completed)}</span>
                    {/if}
                  </div>
                </div>
              {/if}
            {/each}
          </div>
        </section>
      {/if}

      <!-- Overdue carry-forward -->
      {#if data.briefing.overdue_summary?.count > 0}
        <section class="section">
          <div class="section-header">
            <h3 class="section-title">Overdue Carry-Forward</h3>
            <span class="overdue-count-badge">{data.briefing.overdue_summary.count} items</span>
          </div>
          <div class="task-list">
            {#each data.briefing.overdue_summary.top_items as item}
              <div class="task-card overdue">
                <div class="task-header">
                  <span class="task-title">{item.title}</span>
                  <span class="priority-badge {item.priority}">{priorityLabel(item.priority)}</span>
                </div>
                <div class="task-meta">
                  <span class="overdue-label">{item.days_overdue}d overdue</span>
                </div>
              </div>
            {/each}
          </div>
        </section>
      {/if}

    {/if}

    <!-- Yesterday review (collapsible) -->
    {#if data.briefing?.yesterday_review}
      <section class="section collapsible">
        <button class="collapsible-toggle" onclick={() => yesterdayExpanded = !yesterdayExpanded}>
          <h3 class="section-title">Yesterday</h3>
          <span class="toggle-arrow" class:open={yesterdayExpanded}></span>
        </button>
        {#if yesterdayExpanded}
          <div class="yesterday-body">
            <div class="yesterday-stats">
              <span class="yesterday-rate">{Math.round(data.briefing.yesterday_review.completion_rate * 100)}% completed</span>
              <span class="yesterday-counts">{data.briefing.yesterday_review.completed_count}/{data.briefing.yesterday_review.planned_count} planned</span>
            </div>
            {#if data.briefing.yesterday_review.missed?.length > 0}
              <div class="yesterday-missed">
                <span class="missed-label">Missed:</span>
                {#each data.briefing.yesterday_review.missed as item}
                  <span class="missed-chip">{item}</span>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
      </section>
    {/if}

    <!-- Planning patterns (collapsible) -->
    {#if data.patterns?.planning_insight}
      <section class="section collapsible">
        <button class="collapsible-toggle" onclick={() => patternsExpanded = !patternsExpanded}>
          <h3 class="section-title">Patterns</h3>
          <span class="toggle-arrow" class:open={patternsExpanded}></span>
        </button>
        {#if patternsExpanded}
          <div class="patterns-body">
            <p class="insight-text">{data.patterns.planning_insight}</p>
            {#if data.patterns.completion_by_day_of_week}
              <div class="dow-grid">
                {#each Object.entries(data.patterns.completion_by_day_of_week) as [day, rate]}
                  <div class="dow-item">
                    <span class="dow-label">{day.slice(0, 3)}</span>
                    <div class="dow-bar-bg">
                      <div class="dow-bar" style="width: {Math.round(rate * 100)}%"></div>
                    </div>
                    <span class="dow-pct">{Math.round(rate * 100)}%</span>
                  </div>
                {/each}
              </div>
            {/if}
            <div class="patterns-meta">
              <span>{data.patterns.days_with_plans} days tracked</span>
              <span>avg {Math.round((data.patterns.avg_planned_items || 0) * 10) / 10} items/day</span>
              {#if data.patterns.overcommit_days > 0}
                <span class="overcommit-warn">{data.patterns.overcommit_days} overcommit days</span>
              {/if}
            </div>
          </div>
        {/if}
      </section>
    {/if}

  {/if}
</div>

<style>
  .daily-plan-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow-y: auto;
    padding: var(--space-lg);
    gap: var(--space-lg);
  }

  .view-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-md);
  }

  .header-left {
    display: flex;
    align-items: baseline;
    gap: var(--space-md);
  }

  .view-header h2 {
    font-size: var(--text-xl);
    font-weight: 700;
  }

  .header-date {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  .completion-ring {
    display: flex;
    flex-direction: column;
    align-items: center;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: var(--space-sm) var(--space-md);
    min-width: 64px;
  }

  .completion-pct {
    font-size: var(--text-lg);
    font-weight: 700;
    color: var(--accent);
  }

  .completion-label {
    font-size: var(--text-xs);
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }

  .loading, .error-state, .empty-state {
    color: var(--text-muted);
    font-size: var(--text-sm);
    padding: var(--space-md) 0;
  }

  .error-state { color: var(--error); }

  .empty-day {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
    padding: var(--space-xl) 0;
    text-align: center;
    color: var(--text-muted);
  }

  .empty-msg {
    font-size: var(--text-base);
    color: var(--text-secondary);
  }

  .empty-hint {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  /* Sections */
  .section {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .section-title {
    font-size: var(--text-sm);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
  }

  /* Event cards */
  .event-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .event-card {
    display: flex;
    gap: var(--space-md);
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    align-items: flex-start;
  }

  .event-time {
    min-width: 64px;
    flex-shrink: 0;
  }

  .time-label {
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--accent);
  }

  .time-label.muted {
    color: var(--text-muted);
    font-weight: 400;
  }

  .event-body {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .event-title {
    font-size: var(--text-base);
    font-weight: 500;
    color: var(--text-primary);
  }

  .event-meta {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-sm);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .event-location { color: var(--text-secondary); }
  .event-people { color: var(--text-secondary); }

  .event-status {
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--bg-surface-hover);
    color: var(--text-muted);
    font-size: 10px;
    text-transform: capitalize;
  }

  .event-status.upcoming { background: var(--accent-soft, var(--bg-surface-hover)); color: var(--accent); }
  .event-status.attended { background: var(--success-soft); color: var(--success); }
  .event-status.cancelled { background: var(--error-soft); color: var(--error); text-decoration: line-through; }

  /* Plan items */
  .plan-progress {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .plan-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-xs);
  }

  .plan-item {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: var(--space-sm) var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
  }

  .plan-item.completed {
    opacity: 0.6;
  }

  .plan-check {
    font-size: var(--text-sm);
    color: var(--text-muted);
    flex-shrink: 0;
    width: 16px;
  }

  .plan-item.completed .plan-check {
    color: var(--success);
  }

  .plan-desc {
    flex: 1;
    font-size: var(--text-sm);
    color: var(--text-primary);
  }

  .plan-item.completed .plan-desc {
    text-decoration: line-through;
    color: var(--text-muted);
  }

  .plan-node-link {
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-muted);
    font-size: var(--text-xs);
    padding: 1px 4px;
    cursor: pointer;
    flex-shrink: 0;
    transition: all var(--transition-fast);
  }

  .plan-node-link:hover {
    background: var(--bg-surface-hover);
    color: var(--accent);
    border-color: var(--accent);
  }

  .no-plan-prompt {
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px dashed var(--border);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .no-plan-prompt p {
    font-size: var(--text-sm);
    color: var(--text-secondary);
    margin: 0;
  }

  .no-plan-hint {
    font-size: var(--text-xs) !important;
    color: var(--text-muted) !important;
  }

  /* Task cards */
  .task-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .task-card {
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-xs);
  }

  .task-card.overdue {
    border-left: 3px solid var(--error);
  }

  .task-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .task-title {
    font-size: var(--text-base);
    font-weight: 500;
    color: var(--text-primary);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .priority-badge {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 6px;
    border-radius: 99px;
    flex-shrink: 0;
  }

  .priority-badge.high { background: var(--error-soft); color: var(--error); }
  .priority-badge.medium { background: var(--warning-soft); color: var(--warning); }
  .priority-badge.low { background: var(--success-soft); color: var(--success); }

  .task-meta {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .task-project {
    background: var(--bg-surface-hover);
    padding: 1px 6px;
    border-radius: 4px;
    color: var(--text-secondary);
  }

  .overdue-label {
    color: var(--error);
    font-weight: 600;
  }

  .due-today-label {
    color: var(--text-muted);
  }

  .overdue-count-badge {
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 99px;
    background: var(--error-soft);
    color: var(--error);
  }

  /* Habit cards */
  .habit-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
  }

  .habit-card {
    padding: var(--space-md);
    background: var(--bg-surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-xs);
  }

  .habit-card.on-track { border-left: 3px solid var(--success); }
  .habit-card.at-risk { border-left: 3px solid var(--warning); }
  .habit-card.broken { border-left: 3px solid var(--error); }

  .habit-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-sm);
  }

  .habit-title {
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

  .streak-badge.on_track { background: var(--success-soft); color: var(--success); }
  .streak-badge.at_risk { background: var(--warning-soft); color: var(--warning); }
  .streak-badge.broken { background: var(--error-soft); color: var(--error); }

  .habit-meta {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .habit-streak {
    font-weight: 600;
    color: var(--text-secondary);
  }

  /* Collapsible sections */
  .collapsible {
    border-top: 1px solid var(--border);
    padding-top: var(--space-md);
  }

  .collapsible-toggle {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    border: none;
    background: transparent;
    padding: 0;
    color: inherit;
    cursor: pointer;
  }

  .toggle-arrow {
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid var(--text-muted);
    transition: transform var(--transition-fast);
  }

  .toggle-arrow.open { transform: rotate(180deg); }

  /* Yesterday review */
  .yesterday-body {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm);
    padding-top: var(--space-sm);
  }

  .yesterday-stats {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    font-size: var(--text-sm);
  }

  .yesterday-rate {
    font-weight: 600;
    color: var(--text-primary);
  }

  .yesterday-counts {
    color: var(--text-muted);
  }

  .yesterday-missed {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-xs);
    font-size: var(--text-xs);
  }

  .missed-label {
    color: var(--text-muted);
    font-weight: 500;
    flex-shrink: 0;
  }

  .missed-chip {
    padding: 2px 8px;
    background: var(--bg-surface-hover);
    border-radius: 99px;
    color: var(--text-secondary);
  }

  /* Planning patterns */
  .patterns-body {
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
    padding-top: var(--space-sm);
  }

  .insight-text {
    font-size: var(--text-sm);
    color: var(--text-secondary);
    line-height: 1.5;
    margin: 0;
  }

  .dow-grid {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .dow-item {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    font-size: var(--text-xs);
  }

  .dow-label {
    width: 28px;
    color: var(--text-muted);
    flex-shrink: 0;
  }

  .dow-bar-bg {
    flex: 1;
    height: 6px;
    background: var(--bg-surface-hover);
    border-radius: 99px;
    overflow: hidden;
  }

  .dow-bar {
    height: 100%;
    background: var(--accent);
    border-radius: 99px;
    transition: width 0.3s ease;
  }

  .dow-pct {
    width: 32px;
    text-align: right;
    color: var(--text-muted);
    flex-shrink: 0;
  }

  .patterns-meta {
    display: flex;
    align-items: center;
    gap: var(--space-md);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .overcommit-warn {
    color: var(--warning);
    font-weight: 600;
  }
</style>
