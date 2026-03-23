/**
 * Zero-dependency inline text formatter.
 * Converts markdown-like syntax to HTML for rendering via {@html}.
 */

export function formatText(text) {
  if (!text) return '';

  // Escape HTML entities
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Code blocks (``` ... ```) — must come before inline code
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g,
    '<pre class="fmt-code-block"><code>$2</code></pre>');

  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic (single *, not inside bold)
  html = html.replace(/(?<!\*)\*([^*]+?)\*(?!\*)/g, '<em>$1</em>');
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code class="fmt-inline-code">$1</code>');

  // Headings
  html = html.replace(/^### (.+)$/gm, '<h5 class="fmt-h3">$1</h5>');
  html = html.replace(/^## (.+)$/gm, '<h4 class="fmt-h2">$1</h4>');
  html = html.replace(/^# (.+)$/gm, '<h3 class="fmt-h1">$1</h3>');

  // Wikilinks → clickable anchors
  html = html.replace(/\[\[([^\]]+)\]\]/g, '<a class="fmt-wikilink" data-node-id="$1" href="#">$1</a>');

  // Unordered lists — wrap consecutive - items
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
  html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul class="fmt-list">$1</ul>');

  // Numbered lists
  html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');

  // Paragraphs (double newline)
  html = html.replace(/\n\n+/g, '</p><p>');
  // Single newlines (not after block elements)
  html = html.replace(/(?<!<\/(?:h[345]|ul|pre|li|p)>)\n(?!<)/g, '<br>');

  return `<p>${html}</p>`;
}
