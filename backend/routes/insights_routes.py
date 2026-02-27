"""Insights endpoint — proactive AI analysis of the knowledge graph."""

from __future__ import annotations

import logging
import os

import anthropic
from flask import Blueprint, current_app, jsonify

from mentor_agent import load_insights_prompt

logger = logging.getLogger(__name__)

insights_bp = Blueprint("insights", __name__)


@insights_bp.route("/api/insights", methods=["GET"])
def insights():
    mentor = current_app.config.get("mentor")
    if mentor is None:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 503

    g = current_app.config["graph"]
    stats = g.get_stats()
    if stats["total_nodes"] == 0:
        return jsonify({"insights": "Your vault is empty. Add some nodes first and I'll find patterns."})

    all_nodes = g.get_all_nodes()
    summary_parts = []
    for node in all_nodes:
        neighbors = g.get_neighbors(node["id"])
        neighbor_names = [f"{n['title']} ({n['type']})" for n in neighbors]
        connected = f" [connected to: {', '.join(neighbor_names)}]" if neighbor_names else ""
        summary_parts.append(f"- {node.get('title', node['id'])} ({node.get('type', '?')}){connected}")

    graph_summary = "\n".join(summary_parts)

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=1024,
            system=load_insights_prompt(current_app.config["vault_path"]),
            messages=[{"role": "user", "content": f"Here is my knowledge graph:\n{graph_summary}"}],
        )
        return jsonify({"insights": response.content[0].text})
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude API error: {e}"}), 502
