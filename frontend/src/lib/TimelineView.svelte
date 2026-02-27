<script>
  import { getActivity } from './api.js';
  import { getTypeColor } from './colors.js';

  let { onNodeSelect = () => {} } = $props();

  let activities = $state([]);
  let isLoading = $state(true);

  $effect(() => {
    isLoading = true;
    getActivity(50).then(data => {
      activities = data.activities || [];
    }).catch(() => {
      activities = [];
    }).finally(() => {
      isLoading = false;
    });
  });

  function getGroupedActivities() {
    if (!activities.length) return [];
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today); yesterday.setDate(yesterday.getDate() - 1);
    const weekAgo = new Date(today); weekAgo.setDate(weekAgo.getDate() - 7);

    const groups = { Today: [], Yesterday: [], 'This Week': [], Earlier: [] };
    for (const a of activities) {
      const d = new Date(a.timestamp);
      if (d >= today) groups.Today.push(a);
      else if (d >= yesterday) groups.Yesterday.push(a);
      else if (d >= weekAgo) groups['This Week'].push(a);
      else groups.Earlier.push(a);
    }
    return Object.entries(groups).filter(([, items]) => items.length > 0);
  }

  function formatTime(timestamp) {
    const d = new Date(timestamp);
    const now = new Date();
    const diff = now - d;
    const hours = Math.floor(diff / 3600000);
    if (hours < 1) return 'just now';
    if (hours < 24) return `${hours}h ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
</script>

<div class="timeline-view">
  <div class="timeline-header">
    <h2>Activity</h2>
  </div>

  {#if isLoading}
    <div class="loading">Loading activity...</div>
  {:else if activities.length === 0}
    <div class="empty-state">
      <p>No activity yet. Start chatting with Athena to build your graph.</p>
    </div>
  {:else}
    <div class="timeline-list">
      {#each getGroupedActivities() as [groupName, groupActivities]}
        <div class="group-label">{groupName}</div>
        {#each groupActivities as activity}
          <button class="activity-row" onclick={() => onNodeSelect(activity.node_id)}>
            <span class="action-icon" class:created={activity.action === 'created'} class:updated={activity.action === 'updated'}>
              {activity.action === 'created' ? '+' : '~'}
            </span>
            <span class="type-dot" style="background: {getTypeColor(activity.type)}"></span>
            <span class="activity-title">{activity.title}</span>
            <span class="activity-type" style="color: {getTypeColor(activity.type)}">{activity.type}</span>
            <span class="activity-time">{formatTime(activity.timestamp)}</span>
          </button>
        {/each}
      {/each}
    </div>
  {/if}
</div>

<style>
  .timeline-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow-y: auto;
    padding: var(--space-lg);
  }

  .timeline-header {
    margin-bottom: var(--space-lg);
  }

  .timeline-header h2 {
    font-size: var(--text-xl);
    font-weight: 700;
  }

  .loading, .empty-state {
    display: flex;
    justify-content: center;
    padding: var(--space-xl);
    color: var(--text-muted);
  }

  .timeline-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .group-label {
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
    padding: var(--space-md) 0 var(--space-xs);
    font-weight: 600;
  }

  .group-label:first-child {
    padding-top: 0;
  }

  .activity-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border: none;
    border-radius: var(--radius);
    background: transparent;
    color: var(--text-primary);
    font-size: var(--text-sm);
    text-align: left;
    cursor: pointer;
    transition: background var(--transition-fast);
    width: 100%;
  }

  .activity-row:hover {
    background: var(--bg-surface);
  }

  .action-icon {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 700;
    flex-shrink: 0;
  }

  .action-icon.created {
    background: rgba(74, 222, 128, 0.15);
    color: #4ade80;
  }

  .action-icon.updated {
    background: rgba(96, 165, 250, 0.15);
    color: #60a5fa;
  }

  .type-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .activity-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 500;
  }

  .activity-type {
    font-size: var(--text-xs);
    opacity: 0.7;
    flex-shrink: 0;
  }

  .activity-time {
    font-size: var(--text-xs);
    color: var(--text-muted);
    flex-shrink: 0;
  }
</style>
