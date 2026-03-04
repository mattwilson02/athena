"""Vault write / update / rebuild / repair / audit endpoints."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from services.audit_service import audit_vault

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


@vault_bp.route("/api/vault/audit", methods=["POST"])
def vault_audit():
    result = audit_vault(
        current_app.config["graph"],
        current_app.config["schema"],
        current_app.config["vault_path"],
    )
    return jsonify(result)
