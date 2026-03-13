<script>
  import { streamMessage, getSession, getGraph } from './api.js';
  import { getTypeColor } from './colors.js';
  import { formatText } from './format.js';
  import GraphUpdateCard from './GraphUpdateCard.svelte';

  let { sessionId = null, onSessionUpdate = () => {}, onNodeSelect = () => {}, nodeMap = {} } = $props();

  let messages = $state([]);
  let inputText = $state('');
  let isLoading = $state(false);
  let isStreaming = $state(false);
  let messagesContainer = $state(null);
  let loadedSessionId = $state(null);
  let abortController = $state(null);
  let prevMessageCount = $state(0);

  const starterPrompts = [
    "What do you see in my graph?",
    "What am I neglecting?",
    "What connections am I missing?",
    "I want to tell you about something",
  ];

  function scrollToBottom() {
    if (messagesContainer) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
  }

  // Load session messages when sessionId changes
  $effect(() => {
    if (!sessionId || sessionId === loadedSessionId) return;
    loadedSessionId = sessionId;
    Promise.all([
      getSession(sessionId),
      getGraph().catch(() => ({ nodes: [] })),
    ]).then(([session, graphData]) => {
      const existingNodeIds = new Set(graphData.nodes.map(n => n.id));
      const dismissedSet = new Set(session.dismissed_updates || []);
      messages = (session.messages || []).map(m => ({
        role: m.role,
        content: m.role === 'assistant' ? stripGraphUpdates(m.content) : m.content,
        graphUpdates: (m.graph_updates || []).map(u => {
          const key = u.node_id || u.source || `${u.action}-${u.title}`;
          return {
            ...u,
            _alreadyInVault: u.node_id ? existingNodeIds.has(u.node_id) : false,
            _dismissed: dismissedSet.has(key),
          };
        }),
        relevantNodes: m.relevant_nodes || [],
      }));
      setTimeout(scrollToBottom, 50);
    }).catch(() => {
      messages = [];
    });
  });

  // Scroll when new messages are added or during streaming (but not on done/finalize)
  $effect(() => {
    const count = messages.length;
    if (count !== prevMessageCount || isStreaming) {
      prevMessageCount = count;
      setTimeout(scrollToBottom, 50);
    }
  });

  function stripGraphUpdates(text) {
    return text.replace(/<graph_updates>[\s\S]*?<\/graph_updates>/g, '').trim();
  }

  async function send(text) {
    if (!text.trim() || isLoading || !sessionId) return;

    const userMsg = { role: 'user', content: text };
    messages = [...messages, userMsg];
    inputText = '';
    isLoading = true;
    isStreaming = true;

    // Push a placeholder assistant message for streaming
    let streamingContent = '';
    messages = [...messages, {
      role: 'assistant',
      content: '',
      isStreaming: true,
    }];

    abortController = streamMessage(sessionId, text, {
      onText(token) {
        streamingContent += token;
        const last = messages[messages.length - 1];
        messages = [...messages.slice(0, -1), { ...last, content: streamingContent }];
      },
      onDone(event) {
        // Finalize the streaming message with graph updates, relevant nodes, and conflicts
        messages = [...messages.slice(0, -1), {
          role: 'assistant',
          content: event.response,
          graphUpdates: event.graph_updates || [],
          relevantNodes: event.relevant_nodes || [],
          conflicts: event.conflicts || [],
        }];
        isLoading = false;
        isStreaming = false;
        abortController = null;
        onSessionUpdate();
      },
      onError(err) {
        // Replace streaming placeholder with error
        messages = [...messages.slice(0, -1), {
          role: 'assistant',
          content: err.message || 'Something went wrong. Try again.',
          isError: true,
          _retryText: text,
        }];
        isLoading = false;
        isStreaming = false;
        abortController = null;
      },
    });
  }

  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send(inputText);
    }
  }

  function autoResize(e) {
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 150) + 'px';
  }
</script>

<div class="chat-view">
  <div class="messages" bind:this={messagesContainer}>
    {#if messages.length === 0 && !isLoading}
      <div class="empty-state">
        <div class="empty-avatar">A</div>
        <h3>Athena</h3>
        <p>Tell me what's going on. I'll remember everything and connect the dots.</p>
        <div class="starter-prompts">
          {#each starterPrompts as prompt}
            <button class="starter-chip" onclick={() => send(prompt)}>{prompt}</button>
          {/each}
        </div>
      </div>
    {/if}

    {#each messages as msg}
      <div class="message {msg.role}" class:error={msg.isError}>
        {#if msg.conflicts?.length > 0}
          <div class="conflicts">
            <span class="conflicts-label">Conflicts detected</span>
            {#each msg.conflicts as conflict}
              <div class="conflict-chip" class:hard={conflict.severity === 'hard'} class:soft={conflict.severity === 'soft'}>
                <span class="conflict-severity">{conflict.severity}</span>
                <span class="conflict-type">{conflict.conflict_type.replace(/_/g, ' ')}</span>
                <button class="conflict-node" onclick={() => onNodeSelect(conflict.node_id)}>
                  {conflict.title}
                </button>
              </div>
            {/each}
          </div>
        {/if}
        <div class="bubble">
          {#if msg.role === 'assistant' && !msg.isError}
            {@html formatText(msg.content)}{#if msg.isStreaming}<span class="stream-cursor"></span>{/if}
          {:else}
            {msg.content}
          {/if}
        </div>

        {#if msg.isError && msg._retryText}
          <button class="retry-btn" onclick={() => {
            const retryText = msg._retryText;
            const idx = messages.indexOf(msg);
            messages = messages.filter((m, i) => i !== idx && !(i === idx - 1 && m.role === 'user'));
            send(retryText);
          }}>Retry</button>
        {/if}

        {#if msg.graphUpdates?.length > 0}
          <div class="graph-updates">
            {#each msg.graphUpdates as update}
              <GraphUpdateCard {update} {sessionId} {nodeMap} {onNodeSelect}
                onCascade={(proposals) => {
                  msg.graphUpdates = [...msg.graphUpdates, ...proposals];
                }}
              />
            {/each}
          </div>
        {/if}

        {#if msg.relevantNodes?.length > 0}
          <div class="relevant-nodes">
            <span class="relevant-label">Context</span>
            {#each msg.relevantNodes as node}
              <button
                class="node-chip"
                style="border-left-color: {getTypeColor(node.type)}"
                onclick={() => onNodeSelect(node.id)}
              >
                <span class="chip-type">{node.type}</span>
                <span class="chip-title">{node.title}</span>
              </button>
            {/each}
          </div>
        {/if}
      </div>
    {/each}

    {#if isLoading && !isStreaming}
      <div class="message assistant">
        <div class="bubble loading">
          <span class="thinking-text">Thinking</span>
          <span class="thinking-dots">
            <span class="dot"></span><span class="dot"></span><span class="dot"></span>
          </span>
        </div>
      </div>
    {/if}
  </div>

  <div class="input-area">
    <textarea
      bind:value={inputText}
      onkeydown={handleKeydown}
      oninput={autoResize}
      placeholder="What's on your mind?"
      rows="1"
      disabled={isLoading}
    ></textarea>
    <button class="btn-send" onclick={() => send(inputText)} disabled={isLoading || !inputText.trim()}>
      Send
    </button>
  </div>
</div>

<style>
  .chat-view {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .messages {
    flex: 1;
    overflow-y: auto;
    padding: var(--space-lg);
    display: flex;
    flex-direction: column;
    gap: var(--space-md);
  }

  /* ── Empty state ── */

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    flex: 1;
    text-align: center;
    gap: var(--space-sm);
    color: var(--text-secondary);
  }

  .empty-avatar {
    width: 52px;
    height: 52px;
    border-radius: 50%;
    background: var(--accent);
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 26px;
    font-weight: 700;
    margin-bottom: var(--space-xs);
  }

  .empty-state h3 {
    font-size: var(--text-xl);
    color: var(--text-primary);
  }

  .empty-state p {
    max-width: 360px;
    line-height: 1.5;
  }

  .starter-prompts {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-sm);
    justify-content: center;
    margin-top: var(--space-md);
  }

  .starter-chip {
    padding: 10px 18px;
    border: 1px solid var(--border);
    border-radius: 20px;
    background: var(--bg-surface);
    color: var(--text-primary);
    font-size: var(--text-sm);
    transition: all var(--transition-normal);
  }

  .starter-chip:hover {
    border-color: var(--accent);
    background: var(--bg-surface-hover);
    transform: translateY(-1px);
  }

  /* ── Messages ── */

  .message {
    display: flex;
    flex-direction: column;
    animation: msg-in 0.2s ease;
  }

  @keyframes msg-in {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .message.user {
    align-self: flex-end;
    max-width: 65%;
  }

  .message.assistant {
    align-self: flex-start;
    max-width: 85%;
  }

  .bubble {
    padding: 12px 16px;
    border-radius: var(--radius-lg);
    font-size: var(--text-base);
    line-height: 1.7;
    word-wrap: break-word;
    overflow-wrap: break-word;
  }

  .message.user .bubble {
    background: var(--accent);
    color: white;
    border-bottom-right-radius: 4px;
    white-space: pre-wrap;
  }

  .message.assistant .bubble {
    background: var(--bg-surface);
    border-bottom-left-radius: 4px;
  }

  /* Formatted content inside assistant bubbles */
  .message.assistant .bubble :global(p) {
    margin-bottom: 0.4em;
  }

  .message.assistant .bubble :global(p:last-child) {
    margin-bottom: 0;
  }

  .message.assistant .bubble :global(strong) {
    color: var(--text-primary);
    font-weight: 600;
  }

  .message.assistant .bubble :global(.fmt-inline-code) {
    background: var(--bg-primary);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: var(--font-mono);
    font-size: 0.88em;
    color: var(--accent);
  }

  .message.assistant .bubble :global(.fmt-code-block) {
    background: var(--bg-primary);
    padding: 12px;
    border-radius: var(--radius);
    font-family: var(--font-mono);
    font-size: 0.88em;
    overflow-x: auto;
    margin: 0.5em 0;
  }

  .message.assistant .bubble :global(.fmt-h1),
  .message.assistant .bubble :global(.fmt-h2),
  .message.assistant .bubble :global(.fmt-h3) {
    font-weight: 600;
    color: var(--text-primary);
    margin: 0.8em 0 0.3em;
  }

  .message.assistant .bubble :global(.fmt-h1) { font-size: var(--text-lg); }
  .message.assistant .bubble :global(.fmt-h2) { font-size: var(--text-base); }

  .message.assistant .bubble :global(.fmt-list) {
    padding-left: 1.4em;
    margin: 0.4em 0;
  }

  .message.assistant .bubble :global(li) {
    margin: 0.15em 0;
  }

  .message.assistant .bubble :global(.fmt-wikilink) {
    color: var(--accent);
    font-weight: 500;
  }

  /* ── Error state ── */

  .message.error .bubble {
    background: var(--error-soft);
    color: var(--error);
  }

  .retry-btn {
    margin-top: 6px;
    padding: 4px 14px;
    border: 1px solid rgba(248, 113, 113, 0.25);
    border-radius: var(--radius);
    background: transparent;
    color: var(--error);
    font-size: var(--text-sm);
    cursor: pointer;
    align-self: flex-start;
    transition: all var(--transition-fast);
  }

  .retry-btn:hover {
    background: var(--error-soft);
    border-color: var(--error);
  }

  /* ── Loading ── */

  .bubble.loading {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    padding: 14px 20px;
  }

  .thinking-text {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  .thinking-dots {
    display: flex;
    gap: 3px;
  }

  .dot {
    width: 6px;
    height: 6px;
    background: var(--text-muted);
    border-radius: 50%;
    animation: bounce 1.4s infinite ease-in-out;
  }

  .dot:nth-child(1) { animation-delay: 0s; }
  .dot:nth-child(2) { animation-delay: 0.2s; }
  .dot:nth-child(3) { animation-delay: 0.4s; }

  @keyframes bounce {
    0%, 80%, 100% { transform: translateY(0); }
    40% { transform: translateY(-5px); }
  }

  /* ── Streaming cursor ── */

  .stream-cursor {
    display: inline-block;
    width: 2px;
    height: 1.1em;
    background: var(--accent);
    margin-left: 2px;
    vertical-align: text-bottom;
    animation: blink 0.8s step-end infinite;
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }

  /* ── Conflicts ── */

  .conflicts {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 8px;
    align-items: center;
  }

  .conflicts-label {
    font-size: var(--text-xs);
    color: var(--warning);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-right: 2px;
    font-weight: 600;
  }

  .conflict-chip {
    font-size: var(--text-xs);
    padding: 5px 10px;
    border-radius: 6px;
    background: var(--warning-soft);
    border: 1px solid rgba(245, 158, 11, 0.3);
    display: flex;
    gap: 6px;
    align-items: center;
    animation: msg-in 0.3s ease;
  }

  .conflict-chip.hard {
    background: var(--error-soft);
    border-color: rgba(248, 113, 113, 0.3);
  }

  .conflict-severity {
    font-weight: 700;
    text-transform: uppercase;
    font-size: 9px;
    letter-spacing: 0.5px;
    padding: 1px 5px;
    border-radius: 3px;
  }

  .conflict-chip.hard .conflict-severity {
    color: var(--error);
    background: rgba(248, 113, 113, 0.15);
  }

  .conflict-chip.soft .conflict-severity {
    color: var(--warning);
    background: rgba(245, 158, 11, 0.15);
  }

  .conflict-type {
    color: var(--text-secondary);
  }

  .conflict-node {
    color: var(--text-primary);
    font-weight: 500;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: rgba(255, 255, 255, 0.2);
    transition: color var(--transition-fast);
  }

  .conflict-node:hover {
    color: var(--accent);
  }

  /* ── Graph updates ── */

  .graph-updates {
    margin-top: var(--space-sm);
    display: flex;
    flex-direction: column;
    gap: var(--space-xs);
  }

  /* ── Relevant nodes ── */

  .relevant-nodes {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
    align-items: center;
  }

  .relevant-label {
    font-size: var(--text-xs);
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-right: 2px;
  }

  .node-chip {
    font-size: var(--text-xs);
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--bg-surface);
    color: var(--text-secondary);
    border: 1px solid var(--border);
    border-left: 3px solid var(--text-muted);
    transition: all var(--transition-fast);
    cursor: pointer;
    display: flex;
    gap: 6px;
    align-items: center;
  }

  .node-chip:hover {
    background: var(--bg-surface-hover);
    color: var(--text-primary);
  }

  .chip-type {
    font-size: 10px;
    color: var(--text-muted);
  }

  .chip-title {
    font-weight: 500;
  }

  /* ── Input area ── */

  .input-area {
    display: flex;
    gap: var(--space-sm);
    padding: var(--space-md) var(--space-lg);
    border-top: 1px solid var(--border);
    background: var(--bg-primary);
  }

  textarea {
    flex: 1;
    padding: 12px 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--bg-surface);
    color: var(--text-primary);
    resize: none;
    outline: none;
    min-height: 44px;
    max-height: 150px;
    transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
  }

  textarea:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }

  textarea::placeholder { color: var(--text-muted); }

  .btn-send {
    padding: 0 20px;
    border: none;
    border-radius: var(--radius-lg);
    background: var(--accent);
    color: white;
    font-weight: 600;
    font-size: var(--text-base);
    transition: background var(--transition-fast), transform var(--transition-fast);
  }

  .btn-send:hover:not(:disabled) {
    background: var(--accent-hover);
    transform: translateY(-1px);
  }

  .btn-send:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
