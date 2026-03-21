"""State inference service — analyzes recent session messages to assess user state."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Signals that indicate cancellation or avoidance behaviour.
_CANCELLATION_SIGNALS: list[str] = [
    "cancel", "skip", "not going to", "can't be bothered", "too tired",
    "forget it", "nevermind", "nah", "pass on",
]

# Signals that indicate new intentions or plans.
_INTENTION_SIGNALS: list[str] = [
    "i want to", "i'm going to", "im going to", "what if", "let's", "new idea",
]


# ── Signal detectors ──


def _brevity_score(messages: list[dict]) -> tuple[float, str]:
    """Score based on average user message length.

    <30 chars avg → 1.0 (high stress), 30-60 chars → 0.5 (mild), >60 chars → 0.0.
    Only counts user messages.
    """
    user_messages = [m for m in messages if m.get("role") == "user"]
    if not user_messages:
        return 0.0, ""

    avg_len = sum(len(m.get("content", "")) for m in user_messages) / len(user_messages)

    if avg_len < 30:
        score = 1.0
    elif avg_len < 60:
        score = 0.5
    else:
        score = 0.0

    detail = f"avg {int(avg_len)} chars over last {len(user_messages)} messages"
    return score, detail


def _late_night_score(messages: list[dict]) -> tuple[float, str]:
    """Score based on messages sent between 00:00–05:00 local time.

    ≥2 late messages → 1.0, 1 late message → 0.5, 0 → 0.0.
    Skips messages without timestamps.
    """
    late_count = 0
    for m in messages:
        ts = m.get("timestamp")
        if not ts:
            continue
        try:
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts)
            elif isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                continue
            if dt.hour < 5:
                late_count += 1
        except (ValueError, TypeError):
            continue

    if late_count >= 2:
        return 1.0, f"{late_count} messages after midnight"
    elif late_count == 1:
        return 0.5, "1 message after midnight"
    return 0.0, ""


def _cancellation_score(messages: list[dict]) -> tuple[float, str]:
    """Score based on cancellation/avoidance language in user messages.

    ≥2 cancellation signals → 1.0, 1 → 0.5, 0 → 0.0.
    """
    user_messages = [m for m in messages if m.get("role") == "user"]
    count = 0
    for m in user_messages:
        content = m.get("content", "").lower()
        if any(signal in content for signal in _CANCELLATION_SIGNALS):
            count += 1

    if count >= 2:
        return 1.0, f"{count} cancellation signals detected"
    elif count == 1:
        return 0.5, "1 cancellation signal detected"
    return 0.0, ""


def _word_overlap(a: str, b: str) -> float:
    """Compute Jaccard similarity of word sets (case-insensitive)."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _repetition_score(messages: list[dict]) -> tuple[float, str]:
    """Score based on repeated questions/topics among user messages.

    Uses >70% word overlap as the similarity threshold.
    ≥2 repeated messages → 1.0, 1 → 0.5, 0 → 0.0.
    """
    user_messages = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if len(user_messages) < 2:
        return 0.0, ""

    repeat_count = 0
    for i, msg in enumerate(user_messages):
        for j, other in enumerate(user_messages):
            if i >= j:
                continue
            if _word_overlap(msg, other) >= 0.7:
                repeat_count += 1
                break  # count each message at most once

    if repeat_count >= 2:
        return 1.0, f"{repeat_count} repeated messages detected"
    elif repeat_count == 1:
        return 0.5, "1 repeated message detected"
    return 0.0, ""


def _idea_density_score(messages: list[dict]) -> tuple[float, str]:
    """Score based on density of new intentions/plans in user messages.

    ≥5 intention signals → 1.0 (high energy), 3-4 → 0.5, <3 → 0.0.
    This is a HIGH energy signal, not stress.
    """
    user_messages = [m for m in messages if m.get("role") == "user"]
    count = 0
    for m in user_messages:
        content = m.get("content", "").lower()
        count += sum(1 for signal in _INTENTION_SIGNALS if signal in content)

    if count >= 5:
        return 1.0, f"{count} new intentions detected"
    elif count >= 3:
        return 0.5, f"{count} new intentions detected"
    return 0.0, ""


# ── Public API ──


def infer_state(messages: list[dict]) -> dict:
    """Analyze recent session messages to assess the user's current state.

    Takes a list of session messages with 'role', 'content', 'timestamp' fields.

    Returns:
        {
            "energy": "high" | "normal" | "low",
            "stress": "none" | "mild" | "elevated",
            "signals": [{"type": str, "detail": str}, ...],
            "confidence": "low" | "medium" | "high",
        }
    """
    _default: dict = {
        "energy": "normal",
        "stress": "none",
        "signals": [],
        "confidence": "low",
    }

    if not messages:
        return _default

    user_messages = [m for m in messages if m.get("role") == "user"]
    if not user_messages:
        return _default

    # Very short session: confidence never exceeds "low"
    few_messages = len(user_messages) <= 2

    # Run all signal detectors
    try:
        brevity_s, brevity_detail = _brevity_score(messages)
        late_s, late_detail = _late_night_score(messages)
        cancel_s, cancel_detail = _cancellation_score(messages)
        repeat_s, repeat_detail = _repetition_score(messages)
        idea_s, idea_detail = _idea_density_score(messages)
    except Exception:
        logger.exception("State signal detection failed — returning default state")
        return _default

    signals: list[dict] = []
    active_stress_signals = 0

    if brevity_s >= 0.5:
        signals.append({"type": "brevity", "detail": brevity_detail})
        active_stress_signals += 1

    if late_s >= 0.5:
        signals.append({"type": "late_night", "detail": late_detail})
        active_stress_signals += 1

    if cancel_s >= 0.5:
        signals.append({"type": "cancellation", "detail": cancel_detail})
        active_stress_signals += 1

    if repeat_s >= 0.5:
        signals.append({"type": "repetition", "detail": repeat_detail})
        active_stress_signals += 1

    idea_active = idea_s >= 0.5
    if idea_active:
        signals.append({"type": "idea_density", "detail": idea_detail})

    total_active = active_stress_signals + (1 if idea_active else 0)

    # Confidence: very short session is always "low"
    if few_messages or total_active <= 1:
        confidence = "low"
    elif total_active == 2:
        confidence = "medium"
    else:
        confidence = "high"

    # Stress from stress-specific signals
    if active_stress_signals == 0:
        stress = "none"
    elif active_stress_signals == 1:
        stress = "mild"
    else:
        stress = "elevated"

    # Energy
    if stress == "elevated":
        energy = "low"
    elif idea_active and stress == "none":
        energy = "high"
    else:
        energy = "normal"

    logger.debug(
        f"State inferred: energy={energy}, stress={stress}, confidence={confidence}, "
        f"signals={[s['type'] for s in signals]}"
    )

    return {
        "energy": energy,
        "stress": stress,
        "signals": signals,
        "confidence": confidence,
    }
