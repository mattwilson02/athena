"""Chat session and message endpoints."""

from __future__ import annotations

import json

from flask import Blueprint, Response, current_app, jsonify, request

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/api/chat/sessions", methods=["GET"])
def list_sessions():
    return jsonify({"sessions": current_app.config["chat_store"].list_sessions()})


@chat_bp.route("/api/chat/sessions", methods=["POST"])
def create_session():
    session = current_app.config["chat_store"].create_session()
    return jsonify(session), 201


@chat_bp.route("/api/chat/sessions/<session_id>", methods=["GET"])
def get_session(session_id: str):
    session = current_app.config["chat_store"].get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session)


@chat_bp.route("/api/chat/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id: str):
    if not current_app.config["chat_store"].delete_session(session_id):
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"ok": True})


@chat_bp.route("/api/chat/sessions/<session_id>", methods=["PATCH"])
def rename_session(session_id: str):
    data = request.json or {}
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    if not current_app.config["chat_store"].rename_session(session_id, title):
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"ok": True})


@chat_bp.route("/api/chat/sessions/<session_id>/dismiss", methods=["POST"])
def dismiss_update(session_id: str):
    data = request.json or {}
    update_key = data.get("update_key")
    if not update_key:
        return jsonify({"error": "update_key is required"}), 400
    if not current_app.config["chat_store"].dismiss_update(session_id, update_key):
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"ok": True})


@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    data = request.json
    if not data or not data.get("message", "").strip():
        return jsonify({"error": "message is required"}), 400

    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    result = current_app.config["chat_service"].send_message(session_id, data["message"])

    status = result.pop("status", 200)
    if "error" in result:
        return jsonify(result), status
    return jsonify(result)


@chat_bp.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    data = request.json
    if not data or not data.get("message", "").strip():
        return jsonify({"error": "message is required"}), 400

    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    # Capture references before entering the generator (request context won't be available)
    chat_service = current_app.config["chat_service"]
    message = data["message"]

    def generate():
        try:
            for event_type, event_data in chat_service.stream_message(session_id, message):
                yield f"data: {json.dumps({'type': event_type, **event_data})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
