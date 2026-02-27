"""Audit logging middleware."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from flask import request


def log_audit(response):
    """Flask after_request hook — log write operations."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return response

    log_path = os.getenv("AUDIT_LOG", "/logs/audit.log")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "user": getattr(request, "auth_user", "anonymous"),
        "status": response.status_code,
        "ip": request.remote_addr,
    }

    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Don't crash the request if audit logging fails

    return response
