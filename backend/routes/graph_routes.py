"""Graph, node, search, and schema endpoints."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date as _date

import anthropic
from flask import Blueprint, current_app, jsonify, request

from services.accountability_service import calculate_streaks, find_overdue_commitments, check_fundamentals
from services.relationship_service import scan_mentions, assess_relationship_health

logger = logging.getLogger(__name__)

graph_bp = Blueprint("graph", __name__)


@graph_bp.route("/api/graph", methods=["GET"])
def get_graph():
    g = current_app.config["graph"]
    return jsonify({"nodes": g.get_all_nodes(), "edges": g.get_all_edges()})


@graph_bp.route("/api/graph/stats", methods=["GET"])
def get_graph_stats():
    return jsonify(current_app.config["graph"].get_stats())


@graph_bp.route("/api/node/<node_id>", methods=["GET"])
def get_node(node_id: str):
    g = current_app.config["graph"]
    node = g.get_node(node_id)
    if node is None:
        return jsonify({"error": "Node not found"}), 404
    neighbors = g.get_neighbors_with_edges(node_id)
    return jsonify({"node": node, "neighbors": neighbors})


@graph_bp.route("/api/nodes", methods=["GET"])
def get_nodes_by_type():
    g = current_app.config["graph"]
    schema = current_app.config["schema"]
    node_type = request.args.get("type")
    domain = request.args.get("domain")

    if node_type:
        return jsonify({"nodes": g.get_nodes_by_type(node_type)})

    if domain:
        domain_info = schema["domains"].get(domain)
        if not domain_info:
            return jsonify({"error": f"Unknown domain '{domain}'"}), 400
        domain_types = domain_info.get("types", [])
        nodes = []
        for dt in domain_types:
            nodes.extend(g.get_nodes_by_type(dt))
        return jsonify({"nodes": nodes})

    return jsonify({"nodes": g.get_all_nodes()})


@graph_bp.route("/api/schema", methods=["GET"])
def get_schema():
    return jsonify(current_app.config["schema"])


@graph_bp.route("/api/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    g = current_app.config["graph"]
    vi = current_app.config["vector_index"]

    # 1. Title substring matching
    query_lower = query.lower()
    title_matches = []
    for node in g.get_all_nodes():
        title = node.get("title", "")
        if query_lower in title.lower():
            title_matches.append({
                "id": node["id"],
                "title": title,
                "type": node.get("type", "unknown"),
                "score": 0,
                "match_type": "title",
            })
    title_matches = title_matches[:5]

    # 2. Semantic search
    semantic_results = vi.search(query, n=10)
    title_ids = {m["id"] for m in title_matches}
    semantic_deduped = [
        {**r, "match_type": "semantic"}
        for r in semantic_results
        if r["id"] not in title_ids
    ]

    # 3. Merge: title first, then semantic
    results = title_matches + semantic_deduped
    return jsonify({"results": results[:15]})


@graph_bp.route("/api/activity", methods=["GET"])
def activity():
    g = current_app.config["graph"]
    limit = request.args.get("limit", 50, type=int)

    activities = []
    for node in g.get_all_nodes():
        nid = node["id"]
        title = node.get("title", nid)
        ntype = node.get("type", "unknown")
        created = node.get("created")
        updated = node.get("updated")

        if created:
            activities.append({
                "node_id": nid,
                "title": title,
                "type": ntype,
                "action": "created",
                "timestamp": str(created),
            })
        if updated and updated != created:
            activities.append({
                "node_id": nid,
                "title": title,
                "type": ntype,
                "action": "updated",
                "timestamp": str(updated),
            })

    activities.sort(key=lambda a: a["timestamp"], reverse=True)
    return jsonify({"activities": activities[:limit]})


@graph_bp.route("/api/accountability", methods=["GET"])
def get_accountability():
    """Return current accountability state: streaks, overdue commitments, fundamentals, summary."""
    g = current_app.config["graph"]
    try:
        streaks = calculate_streaks(g)
        overdue = find_overdue_commitments(g, _date.today())
    except Exception:
        logger.exception("Accountability service error")
        return jsonify({"error": "Failed to compute accountability state"}), 500

    try:
        # include_active=True so the dashboard shows a complete picture of all 6 fundamentals
        fundamentals = check_fundamentals(g, _date.today(), include_active=True)
    except Exception:
        logger.exception("Fundamentals check error")
        return jsonify({"error": "Failed to compute fundamentals state"}), 500

    on_track = sum(1 for s in streaks if s["streak_status"] == "on_track")
    at_risk = sum(1 for s in streaks if s["streak_status"] == "at_risk")
    broken = sum(1 for s in streaks if s["streak_status"] == "broken")
    oldest_overdue = max((o["days_overdue"] for o in overdue), default=0)

    f_active = sum(1 for f in fundamentals if f["status"] == "active")
    f_neglected = sum(1 for f in fundamentals if f["status"] == "neglected")
    f_no_data = sum(1 for f in fundamentals if f["status"] == "no_data")

    # Relationships summary (best-effort — non-fatal on error)
    relationships_summary: dict = {
        "total_persons": 0,
        "active": 0,
        "drifting": 0,
        "neglected": 0,
        "no_data": 0,
        "most_mentioned": None,
    }
    try:
        chat_store = current_app.config.get("chat_store")
        if chat_store is not None:
            mention_stats = scan_mentions(chat_store, g)
            relationships = assess_relationship_health(mention_stats, _date.today())
            r_active = sum(1 for r in relationships if r["health"] == "active")
            r_drifting = sum(1 for r in relationships if r["health"] == "drifting")
            r_neglected = sum(1 for r in relationships if r["health"] == "neglected")
            r_no_data = sum(1 for r in relationships if r["health"] == "no_data")
            most_mentioned = None
            if relationships:
                top = max(relationships, key=lambda r: r["mention_count"])
                if top["mention_count"] > 0:
                    most_mentioned = top["person_id"]
            relationships_summary = {
                "total_persons": len(relationships),
                "active": r_active,
                "drifting": r_drifting,
                "neglected": r_neglected,
                "no_data": r_no_data,
                "most_mentioned": most_mentioned,
            }
    except Exception:
        logger.exception("Relationship summary failed — returning empty summary")

    return jsonify({
        "streaks": streaks,
        "overdue": overdue,
        "summary": {
            "total_habits": len(streaks),
            "on_track": on_track,
            "at_risk": at_risk,
            "broken": broken,
            "overdue_count": len(overdue),
            "oldest_overdue_days": oldest_overdue,
        },
        "fundamentals": fundamentals,
        "fundamentals_summary": {
            "total": len(fundamentals),
            "active": f_active,
            "neglected": f_neglected,
            "no_data": f_no_data,
        },
        "relationships_summary": relationships_summary,
    })


@graph_bp.route("/api/relationships", methods=["GET"])
def get_relationships():
    """Return full relationship health data for all person nodes."""
    g = current_app.config["graph"]
    chat_store = current_app.config.get("chat_store")
    if chat_store is None:
        return jsonify({"error": "Chat store not configured"}), 503

    try:
        mention_stats = scan_mentions(chat_store, g)
        relationships = assess_relationship_health(mention_stats, _date.today())
    except Exception:
        logger.exception("Relationship service error")
        return jsonify({"error": "Failed to compute relationship data"}), 500

    # Sort: neglected first, then drifting, then active/no_data; within tier by influence desc
    _health_order = {"neglected": 0, "drifting": 1, "active": 2, "no_data": 3}
    relationships.sort(
        key=lambda r: (_health_order.get(r["health"], 3), -r.get("influence_score", 0))
    )

    # Build summary
    r_active = sum(1 for r in relationships if r["health"] == "active")
    r_drifting = sum(1 for r in relationships if r["health"] == "drifting")
    r_neglected = sum(1 for r in relationships if r["health"] == "neglected")
    r_no_data = sum(1 for r in relationships if r["health"] == "no_data")

    most_mentioned = None
    if relationships:
        top = max(relationships, key=lambda r: r["mention_count"])
        if top["mention_count"] > 0:
            most_mentioned = top["person_id"]

    highest_influence = [
        r["person_id"]
        for r in sorted(relationships, key=lambda r: r["influence_score"], reverse=True)
        if r["mention_count"] > 0
    ][:3]

    summary = {
        "total_persons": len(relationships),
        "active": r_active,
        "drifting": r_drifting,
        "neglected": r_neglected,
        "no_data": r_no_data,
        "most_mentioned": most_mentioned,
        "highest_influence": highest_influence,
    }

    return jsonify({"relationships": relationships, "summary": summary})


@graph_bp.route("/api/debug/retrieval", methods=["GET"])
def debug_retrieval():
    """Return full retrieval diagnostic for a query — intent, scores, tier breakdown."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    mentor = current_app.config.get("mentor")
    if mentor is None:
        return jsonify({"error": "Mentor service not configured"}), 503

    try:
        result = mentor.get_context_debug(query)
        return jsonify(result)
    except Exception as exc:
        logger.exception("Retrieval debug error")
        return jsonify({"error": f"Retrieval failed: {exc}"}), 500


@graph_bp.route("/api/graph/suggest-links", methods=["POST"])
def suggest_links():
    data = request.json or {}
    node_id = data.get("node_id")

    if node_id:
        suggestions = current_app.config["vault_service"].find_cross_references(node_id)
        return jsonify({"suggestions": suggestions})

    # Full graph AI analysis
    client = current_app.config.get("claude_client")
    if client is None:
        return jsonify({"error": "Claude API not configured"}), 503

    g = current_app.config["graph"]
    stats = g.get_stats()
    if stats["total_nodes"] < 2:
        return jsonify({"suggestions": []})

    all_nodes = g.get_all_nodes()
    all_edges = g.get_all_edges()

    node_lines = []
    for n in all_nodes:
        degree = g.get_degree(n["id"])
        node_lines.append(
            f'- {n["id"]} ({n.get("type", "?")}) "{n.get("title", "")}" [degree={degree}]'
        )

    edge_lines = [f'- {e["source"]} --{e["type"]}--> {e["target"]}' for e in all_edges]

    prompt = (
        f"Nodes ({len(all_nodes)}):\n" + "\n".join(node_lines)
        + f"\n\nEdges ({len(all_edges)}):\n"
        + ("\n".join(edge_lines) if edge_lines else "(none)")
        + "\n\nSuggest up to 10 missing edges. Focus on: orphaned nodes (degree=0), "
        "nodes that clearly relate but aren't linked, cross-domain connections.\n\n"
        "Return ONLY a JSON array:\n"
        '[{"source": "node-id", "target": "node-id", "type": "edge_type", "reason": "why"}]'
    )

    try:
        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=1024,
            system="You are a graph analysis tool. Return only valid JSON arrays.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            suggestions = json.loads(json_match.group())
            valid_ids = {n["id"] for n in all_nodes}
            suggestions = [
                s for s in suggestions
                if isinstance(s, dict)
                and s.get("source") in valid_ids
                and s.get("target") in valid_ids
            ]
            return jsonify({"suggestions": suggestions})
        return jsonify({"suggestions": []})
    except (anthropic.APIError, json.JSONDecodeError) as e:
        logger.error(f"suggest-links error: {e}")
        return jsonify({"error": "Failed to generate suggestions. Try again."}), 502
