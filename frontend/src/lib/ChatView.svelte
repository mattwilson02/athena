<script>
  import { sendMessage, getSession, getGraph } from './api.js';
  import GraphUpdateCard from './GraphUpdateCard.svelte';

  let { sessionId = null, onSessionUpdate = () => {} } = $props();

  let messages = $state([]);
  let inputText = $state('');
  let isLoading = $state(false);
  let messagesContainer = $state(null);
  let loadedSessionId = $state(null);

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

  // Scroll when messages change
  $effect(() => {
    messages;
    setTimeout(scrollToBottom, 50);
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

    try {
      const result = await sendMessage(sessionId, text);
      messages = [...messages, {
        role: 'assistant',
        content: result.response,
        graphUpdates: result.graph_updates || [],
        relevantNodes: result.relevant_nodes || [],
      }];
      onSessionUpdate();
    } catch (err) {
      messages = [...messages, {
        role: 'assistant',
        content: err.message || 'Something went wrong. Try again.',
        isError: true,
        _retryText: text,
      }];
    } finally {
      isLoading = false;
    }
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
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }
</script>

<div class="chat-view">
  <div class="messages" bind:this={messagesContainer}>
    {#if messages.length === 0 && !isLoading}
      <div class="empty-state">
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
        <div class="bubble">
          {msg.content}
        </div>

        {#if msg.isError && msg._retryText}
          <button class="retry-btn" onclick={() => {
            const retryText = msg._retryText;
            // Remove the error message and the user message before it
            const idx = messages.indexOf(msg);
            messages = messages.filter((m, i) => i !== idx && !(i === idx - 1 && m.role === 'user'));
            send(retryText);
          }}>Retry</button>
        {/if}

        {#if msg.graphUpdates?.length > 0}
          <div class="graph-updates">
            {#each msg.graphUpdates as update}
              <GraphUpdateCard {update} {sessionId} />
            {/each}
          </div>
        {/if}

        {#if msg.relevantNodes?.length > 0}
          <div class="relevant-nodes">
            {#each msg.relevantNodes as node}
              <span class="node-chip">{node.title}</span>
            {/each}
          </div>
        {/if}
      </div>
    {/each}

    {#if isLoading}
      <div class="message assistant">
        <div class="bubble loading">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
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
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    flex: 1;
    text-align: center;
    gap: 12px;
    color: var(--text-secondary);
  }

  .empty-state h3 {
    font-size: 20px;
    color: var(--text-primary);
  }

  .starter-prompts {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
    margin-top: 8px;
  }

  .starter-chip {
    padding: 8px 16px;
    border: 1px solid var(--border);
    border-radius: 20px;
    background: var(--bg-surface);
    color: var(--text-primary);
    font-size: 13px;
    transition: all 0.15s;
  }

  .starter-chip:hover {
    border-color: var(--accent);
    background: var(--bg-surface-hover);
  }

  .message {
    display: flex;
    flex-direction: column;
    max-width: 720px;
  }

  .message.user {
    align-self: flex-end;
  }

  .message.assistant {
    align-self: flex-start;
  }

  .bubble {
    padding: 12px 16px;
    border-radius: var(--radius-lg);
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-wrap: break-word;
  }

  .message.user .bubble {
    background: var(--accent);
    color: white;
    border-bottom-right-radius: 4px;
  }

  .message.assistant .bubble {
    background: var(--bg-surface);
    border-bottom-left-radius: 4px;
  }

  .message.error .bubble {
    background: #f8717120;
    color: #f87171;
  }

  .retry-btn {
    margin-top: 6px;
    padding: 4px 14px;
    border: 1px solid #f8717140;
    border-radius: var(--radius);
    background: transparent;
    color: #f87171;
    font-size: 12px;
    cursor: pointer;
    align-self: flex-start;
    transition: all 0.15s;
  }

  .retry-btn:hover {
    background: #f8717115;
    border-color: #f87171;
  }

  .bubble.loading {
    display: flex;
    gap: 4px;
    padding: 16px 20px;
  }

  .dot {
    width: 8px;
    height: 8px;
    background: var(--text-muted);
    border-radius: 50%;
    animation: bounce 1.4s infinite ease-in-out;
  }

  .dot:nth-child(1) { animation-delay: 0s; }
  .dot:nth-child(2) { animation-delay: 0.2s; }
  .dot:nth-child(3) { animation-delay: 0.4s; }

  @keyframes bounce {
    0%, 80%, 100% { transform: translateY(0); }
    40% { transform: translateY(-6px); }
  }

  .graph-updates {
    margin-top: 4px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .relevant-nodes {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;
  }

  .node-chip {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--bg-surface-hover);
    color: var(--text-muted);
  }

  .input-area {
    display: flex;
    gap: 8px;
    padding: 16px 24px;
    border-top: 1px solid var(--border);
    background: var(--bg-primary);
  }

  textarea {
    flex: 1;
    padding: 12px 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--bg-input);
    color: var(--text-primary);
    resize: none;
    outline: none;
    min-height: 44px;
    max-height: 120px;
  }

  textarea:focus { border-color: var(--accent); }

  textarea::placeholder { color: var(--text-muted); }

  .btn-send {
    padding: 0 20px;
    border: none;
    border-radius: var(--radius);
    background: var(--accent);
    color: white;
    font-weight: 600;
    font-size: 14px;
  }

  .btn-send:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-send:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
