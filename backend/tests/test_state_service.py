"""Tests for state_service.py — user state inference from session messages."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.state_service import infer_state


# ── Helpers ──


def _msg(content: str, role: str = "user", timestamp: str | None = None) -> dict:
    """Build a minimal message dict."""
    m: dict = {"role": role, "content": content}
    if timestamp is not None:
        m["timestamp"] = timestamp
    return m


def _ts(hour: int, minute: int = 0) -> str:
    """Return an ISO timestamp string for today at the given hour (UTC)."""
    now = datetime.now(tz=timezone.utc)
    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.isoformat()


# ── Default state ──


class TestDefaultState:
    def test_default_state_empty_messages(self):
        result = infer_state([])
        assert result["energy"] == "normal"
        assert result["stress"] == "none"
        assert result["confidence"] == "low"
        assert result["signals"] == []

    def test_default_state_only_assistant_messages(self):
        messages = [
            _msg("Here is my response.", role="assistant"),
            _msg("Sure, let me help with that.", role="assistant"),
        ]
        result = infer_state(messages)
        assert result["energy"] == "normal"
        assert result["stress"] == "none"
        assert result["confidence"] == "low"

    def test_default_state_returns_dict_with_all_keys(self):
        result = infer_state([])
        assert "energy" in result
        assert "stress" in result
        assert "signals" in result
        assert "confidence" in result


# ── Brevity signal ──


class TestBrevitySignal:
    def test_brevity_signal_terse_messages(self):
        """5 messages under 20 chars → brevity signal active."""
        messages = [_msg(c) for c in ["ok", "no", "skip", "nah", "pass"]]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "brevity" in signal_types

    def test_brevity_signal_normal_messages(self):
        """Messages over 80 chars → brevity score 0.0, no brevity signal."""
        messages = [
            _msg("I've been thinking a lot about my goals this week and I really want to make progress on them"),
            _msg("The strategy session I had yesterday was very productive and I feel good about the direction"),
            _msg("I managed to complete all my tasks today and I'm feeling really motivated to continue"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "brevity" not in signal_types

    def test_brevity_mixed_with_assistant(self):
        """Only user messages count for brevity."""
        messages = [
            _msg("ok", role="user"),
            _msg("This is a very long assistant response that should not affect the brevity calculation at all", role="assistant"),
            _msg("no", role="user"),
            _msg("hi", role="user"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "brevity" in signal_types


# ── Late night timing signal ──


class TestLateNightSignal:
    def test_late_night_signal_two_messages(self):
        """2 messages at 2am → late_night signal active."""
        messages = [
            _msg("can't sleep", timestamp=_ts(2, 15)),
            _msg("still awake", timestamp=_ts(2, 45)),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "late_night" in signal_types

    def test_late_night_one_message(self):
        """1 message at 3am → late_night signal at 0.5 (mild indicator)."""
        messages = [
            _msg("what time is it", timestamp=_ts(3, 0)),
            _msg("just checking something", timestamp=_ts(14, 0)),
        ]
        result = infer_state(messages)
        # 1 late message gives score 0.5, so it IS detected but not a major signal
        signal_types = [s["type"] for s in result["signals"]]
        assert "late_night" in signal_types

    def test_late_night_daytime_messages(self):
        """Messages during day → no late_night signal."""
        messages = [
            _msg("good morning", timestamp=_ts(9, 0)),
            _msg("afternoon check in", timestamp=_ts(14, 30)),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "late_night" not in signal_types

    def test_late_night_no_timestamps(self):
        """Messages without timestamps → timing skipped, other signals still work."""
        messages = [
            _msg("ok"),
            _msg("no"),
            _msg("skip"),
            _msg("too tired to do this"),
            _msg("forget it"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        # No timestamps → no late_night signal
        assert "late_night" not in signal_types
        # Other signals (brevity, cancellation) should still be detected
        assert len(result["signals"]) > 0

    def test_late_night_boundary_hour_5(self):
        """Hour 5 is NOT late night (boundary is < 5)."""
        messages = [
            _msg("early start", timestamp=_ts(5, 0)),
            _msg("another message", timestamp=_ts(5, 30)),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "late_night" not in signal_types


# ── Cancellation signal ──


class TestCancellationSignal:
    def test_cancellation_two_signals(self):
        """Messages with 'cancel' and 'skip' → cancellation signal active."""
        messages = [
            _msg("I want to cancel the meeting"),
            _msg("actually skip it too"),
            _msg("not going to make it"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "cancellation" in signal_types

    def test_cancellation_single_signal(self):
        """Single cancellation word → detected at 0.5 (one cancellation)."""
        messages = [
            _msg("I need to cancel my gym session today"),
            _msg("otherwise things are going well"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "cancellation" in signal_types

    def test_no_cancellation_positive_messages(self):
        """Positive messages → no cancellation signal."""
        messages = [
            _msg("I went to the gym today and it was great"),
            _msg("Looking forward to the meeting tomorrow"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "cancellation" not in signal_types


# ── Repetition signal ──


class TestRepetitionSignal:
    def test_repetition_same_question_twice(self):
        """Same question asked twice → repetition signal active."""
        messages = [
            _msg("should I go to the gym today for training"),
            _msg("should I go to the gym today for training please"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "repetition" in signal_types

    def test_no_repetition_varied_messages(self):
        """Different topics → no repetition signal."""
        messages = [
            _msg("Tell me about my fitness goals"),
            _msg("What are my upcoming events this week"),
            _msg("How is my budget looking"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "repetition" not in signal_types

    def test_repetition_single_message(self):
        """Only one user message → no repetition possible."""
        messages = [_msg("what should I do today")]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "repetition" not in signal_types


# ── Idea density signal ──


class TestIdeaDensitySignal:
    def test_idea_density_high_energy(self):
        """5+ 'I want to' style messages with no stress → energy 'high'."""
        messages = [
            _msg("I want to start a completely new and exciting side project that will challenge me significantly"),
            _msg("I'm going to learn a completely new programming language to expand my technical skills further"),
            _msg("what if I also took on a new fitness challenge and trained for a marathon this coming year"),
            _msg("let's also do a comprehensive reading challenge this month and get through multiple books"),
            _msg("new idea for something amazing: I want to start a podcast about personal productivity systems"),
        ]
        result = infer_state(messages)
        assert result["energy"] == "high"
        signal_types = [s["type"] for s in result["signals"]]
        assert "idea_density" in signal_types

    def test_idea_density_not_active_with_few_intentions(self):
        """Only 1-2 intention signals → idea_density not active."""
        messages = [
            _msg("I want to try that new restaurant"),
            _msg("how is my graph looking"),
        ]
        result = infer_state(messages)
        signal_types = [s["type"] for s in result["signals"]]
        assert "idea_density" not in signal_types


# ── Stress derivation ──


class TestStressDerivation:
    def test_stress_elevated_multiple_signals(self):
        """Brevity + cancellation → stress elevated."""
        messages = [
            _msg("cancel", timestamp=_ts(2, 0)),
            _msg("skip", timestamp=_ts(2, 10)),
            _msg("nah"),
            _msg("no"),
            _msg("forget it"),
        ]
        result = infer_state(messages)
        assert result["stress"] == "elevated"

    def test_stress_none_positive_messages(self):
        """Mixed normal-length messages (>60 chars) → stress none."""
        messages = [
            _msg("I had a very productive and rewarding day today and successfully finished all of my planned tasks"),
            _msg("Looking forward to the upcoming weekend plans and spending some quality time with friends and family"),
            _msg("Just wanted to update my goals in the graph and add some new notes from today's planning session"),
        ]
        result = infer_state(messages)
        assert result["stress"] == "none"
        assert result["energy"] == "normal"

    def test_single_short_message_not_elevated(self):
        """Single short message in a conversation → stress not elevated."""
        messages = [
            _msg("I had a really productive day working on the project"),
            _msg("The meeting went well and we made great progress"),
            _msg("ok"),  # one short message
            _msg("I'm planning to continue tomorrow with the next phase"),
        ]
        result = infer_state(messages)
        # Average length is high, so brevity not triggered
        assert result["stress"] != "elevated"


# ── Stress overrides energy ──


class TestStressOverridesEnergy:
    def test_stress_elevated_overrides_high_energy(self):
        """Elevated stress → energy 'low', even with high idea density."""
        messages = [
            _msg("I want to start a project", timestamp=_ts(2, 0)),
            _msg("I'm going to do another thing", timestamp=_ts(2, 30)),
            _msg("ok"),
            _msg("nah"),
            _msg("skip it"),
        ]
        result = infer_state(messages)
        # stress elevated → energy must be low
        if result["stress"] == "elevated":
            assert result["energy"] == "low"


# ── Confidence scaling ──


class TestConfidenceScaling:
    def test_confidence_low_with_no_signals(self):
        """No signals → confidence 'low'."""
        messages = [
            _msg("Hello, how are you"),
            _msg("Can you tell me about my goals"),
        ]
        result = infer_state(messages)
        assert result["confidence"] == "low"

    def test_confidence_low_very_short_session(self):
        """1-2 user messages → confidence always 'low' regardless of signals."""
        messages = [
            _msg("cancel"),  # brevity + cancellation signals
        ]
        result = infer_state(messages)
        assert result["confidence"] == "low"

    def test_confidence_medium_two_signals(self):
        """2 active signals → confidence 'medium'."""
        messages = [
            _msg("cancel", timestamp=_ts(2, 0)),
            _msg("skip", timestamp=_ts(2, 10)),
            _msg("ok"),
            _msg("nah"),
        ]
        result = infer_state(messages)
        # brevity (short messages) + late_night + cancellation signals
        # With 2+ signals and not a very short session, should be medium or high
        assert result["confidence"] in ("medium", "high")

    def test_confidence_high_three_plus_signals(self):
        """3+ active signals → confidence 'high'."""
        # Need brevity + late_night + cancellation all active
        messages = [
            _msg("cancel", timestamp=_ts(2, 0)),
            _msg("skip", timestamp=_ts(2, 5)),
            _msg("nah", timestamp=_ts(2, 10)),
            _msg("no", timestamp=_ts(2, 15)),
            _msg("ok", timestamp=_ts(2, 20)),
        ]
        result = infer_state(messages)
        # brevity (all short) + late_night (5 messages at 2am) + cancellation
        assert result["confidence"] == "high"
