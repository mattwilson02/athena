"""Vault write / update / rebuild / repair endpoints."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

vault_bp = Blueprint("vault", __name__)


@vault_bp.route("/api/vault/write", methods=["POST"])
def vault_write():
    data = request.json
    if not data:
        return jsonify({"error": "Request body required"}), 400

    result = current_app.config["vault_service"].write(data)

    status = result.pop("status", 200)
    if "error" in result:
        return jsonify(result), status
    return jsonify(result)


@vault_bp.route("/api/vault/update", methods=["POST"])
def vault_update():
    data = request.json
    if not data:
        return jsonify({"error": "Request body required"}), 400

    result = current_app.config["vault_service"].update(data)

    status = result.pop("status", 200)
    if "error" in result:
        return jsonify(result), status
    return jsonify(result)


@vault_bp.route("/api/vault/rebuild", methods=["POST"])
def vault_rebuild():
    stats = current_app.config["rebuild_fn"]()
    return jsonify({"ok": True, "stats": stats})


@vault_bp.route("/api/vault/repair", methods=["POST"])
def vault_repair():
    result = current_app.config["vault_service"].repair()
    return jsonify(result)
