from __future__ import annotations


PERMANENCE_DEFAULTS: dict[str, tuple[str, float]] = {
    # identity (+0.15)
    "value":        ("identity",  0.15),
    "belief":       ("identity",  0.15),
    "fear":         ("identity",  0.15),
    # strategic (+0.10)
    "goal":         ("strategic", 0.10),
    "habit":        ("strategic", 0.10),
    "skill":        ("strategic", 0.10),
    "project":      ("strategic", 0.10),
    # tactical (+0.00)
    "task":         ("tactical",  0.00),
    "reminder":     ("tactical",  0.00),
    "event":        ("tactical",  0.00),
    "expense":      ("tactical",  0.00),
    "subscription": ("tactical",  0.00),
    "budget":       ("tactical",  0.00),
    # ephemeral (-0.05)
    "daily":        ("ephemeral", -0.05),
    "note":         ("ephemeral", -0.05),
}

STATUS_PENALTIES: dict[str, float] = {
    "completed": -0.15,
    "done": -0.15,
    "cancelled": -0.25,
    "archived": -0.25,
    "abandoned": -0.25,
    "superseded": -0.25,
    "parked": -0.10,
}


def get_permanence(node_type: str) -> tuple[str, float]:
    """Return (level_name, boost) for a node type. Unlisted types default to tactical."""
    return PERMANENCE_DEFAULTS.get(node_type, ("tactical", 0.00))
