"""Bearer token authentication + permission scoping middleware."""

from __future__ import annotations

from flask import current_app, jsonify, request

# Endpoints that require authentication (all write operations)
PROTECTED_PREFIXES = [
    ("POST", "/api/chat"),
    ("POST", "/api/vault"),
    ("DELETE", "/api/chat"),
    ("PATCH", "/api/chat"),
]

# Map URL path prefixes to permission groups
# Used to enforce per-user endpoint scoping from config.yaml
PATH_TO_GROUP = {
    "/api/chat": "chat",
    "/api/vault": "vault",
    "/api/graph": "graph",
    "/api/insights": "insights",
    "/api/node": "graph",
    "/api/search": "graph",
    "/api/schema": "graph",
    "/api/activity": "graph",
}


def _get_endpoint_group(path: str) -> str | None:
    """Map a request path to its permission group."""
    for prefix, group in PATH_TO_GROUP.items():
        if path.startswith(prefix):
            return group
    return None


def authenticate():
    """Validate bearer token. Returns username or None."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1]
    auth_tokens = current_app.config.get("auth_tokens", {})
    return auth_tokens.get(token)


def _check_permission(username: str) -> bool:
    """Check if authenticated user is allowed to access this endpoint."""
    config = current_app.config.get("athena_config", {})
    user_config = config.get("users", {}).get(username, {})
    allowed = user_config.get("endpoints", [])

    if "*" in allowed:
        return True

    group = _get_endpoint_group(request.path)
    return group is not None and group in allowed


def require_auth():
    """Flask before_request hook — reject unauthenticated or unauthorised requests."""
    # Health check is always open
    if request.path == "/api/health":
        return None

    # Check if read auth is enabled
    config = current_app.config.get("athena_config", {})
    protect_reads = config.get("auth", {}).get("protect_reads", False)

    is_protected = any(
        request.method == method and request.path.startswith(prefix)
        for method, prefix in PROTECTED_PREFIXES
    )

    # If protect_reads is on, all API endpoints require auth
    if not is_protected and protect_reads and request.path.startswith("/api/"):
        is_protected = True

    if not is_protected:
        return None  # Allow through

    username = authenticate()
    if username is None:
        return jsonify({"error": "Unauthorized"}), 401

    # Check endpoint permission scoping
    if not _check_permission(username):
        return jsonify({"error": "Forbidden"}), 403

    # Store authenticated user for audit logging
    request.auth_user = username
    return None
