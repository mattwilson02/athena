"""Tests for classify_mode() in mentor_agent.py."""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mentor_agent import classify_mode


class TestClassifyMode:
    # ── Mirror (default) ──

    def test_default_mirror(self):
        assert classify_mode("What's on my plate this week?", None) == "mirror"

    def test_plain_question_mirror(self):
        assert classify_mode("What are my active goals?", None) == "mirror"

    def test_status_query_mirror(self):
        assert classify_mode("How many tasks do I have?", []) == "mirror"

    def test_empty_message_mirror(self):
        assert classify_mode("", None) == "mirror"

    def test_empty_message_with_no_conflicts(self):
        assert classify_mode("", []) == "mirror"

    # ── Guardian ──

    def test_guardian_hard_conflict(self):
        conflicts = [{"severity": "hard", "conflict_type": "value_violation"}]
        assert classify_mode("I'm going to skip the gym", conflicts) == "guardian"

    def test_guardian_beats_dialectic(self):
        """Hard conflict overrides dialectic signals."""
        conflicts = [{"severity": "hard", "conflict_type": "value_violation"}]
        assert classify_mode("Should I quit my job?", conflicts) == "guardian"

    def test_guardian_beats_advisor(self):
        """Hard conflict overrides advisor signals."""
        conflicts = [{"severity": "hard", "conflict_type": "habit_break"}]
        assert classify_mode("I want to start a new project", conflicts) == "guardian"

    def test_guardian_not_triggered_by_keywords(self):
        """'I'm going to skip the gym' with no conflicts should NOT be guardian."""
        assert classify_mode("I'm going to skip the gym", []) == "advisor"

    def test_guardian_not_triggered_by_soft_only(self):
        """Soft-only conflicts should not trigger guardian."""
        conflicts = [{"severity": "soft", "conflict_type": "commitment_overload"}]
        result = classify_mode("I'm going to skip the gym", conflicts)
        assert result != "guardian"

    def test_guardian_mixed_hard_and_soft(self):
        """Hard conflict present among mixed severities → guardian."""
        conflicts = [
            {"severity": "soft", "conflict_type": "commitment_overload"},
            {"severity": "hard", "conflict_type": "value_violation"},
        ]
        assert classify_mode("I'm going to miss the meeting", conflicts) == "guardian"

    # ── Dialectic ──

    def test_dialectic_should_i(self):
        assert classify_mode("Should I move to London?", None) == "dialectic"

    def test_dialectic_what_if(self):
        assert classify_mode("What if I took 3 months off?", None) == "dialectic"

    def test_dialectic_considering(self):
        assert classify_mode("I'm considering changing careers", None) == "dialectic"

    def test_dialectic_torn_between(self):
        assert classify_mode("I'm torn between two options", None) == "dialectic"

    def test_dialectic_not_sure(self):
        assert classify_mode("Not sure if I should accept this offer", None) == "dialectic"

    def test_dialectic_debating_whether(self):
        assert classify_mode("I'm debating whether to sell my car", None) == "dialectic"

    def test_dialectic_thinking_about_quitting(self):
        assert classify_mode("I've been thinking about quitting my job", None) == "dialectic"

    def test_dialectic_thinking_about_leaving(self):
        assert classify_mode("I'm thinking about leaving the company", None) == "dialectic"

    def test_dialectic_worth_it_to(self):
        assert classify_mode("Is it worth it to move abroad?", None) == "dialectic"

    def test_dialectic_beats_advisor(self):
        """Dialectic signals take priority over advisor signals."""
        assert classify_mode("Should I start a new project?", None) == "dialectic"

    def test_dialectic_beats_soft_conflicts(self):
        """Dialectic beats soft-only conflicts."""
        conflicts = [{"severity": "soft", "conflict_type": "commitment_overload"}]
        assert classify_mode("Should I take on more work?", conflicts) == "dialectic"

    # ── Advisor ──

    def test_advisor_new_commitment(self):
        assert classify_mode("I want to start meditating", None) == "advisor"

    def test_advisor_i_want_to_start(self):
        assert classify_mode("I want to start learning guitar", None) == "advisor"

    def test_advisor_im_going_to(self):
        assert classify_mode("I'm going to run a marathon", None) == "advisor"

    def test_advisor_planning_to(self):
        assert classify_mode("I'm planning to take a course", None) == "advisor"

    def test_advisor_thinking_about_starting(self):
        assert classify_mode("I'm thinking about starting a blog", None) == "advisor"

    def test_advisor_sign_up_for(self):
        assert classify_mode("I'm going to sign up for a gym membership", None) == "advisor"

    def test_advisor_commit_to(self):
        assert classify_mode("I want to commit to a daily reading habit", None) == "advisor"

    def test_advisor_new_project(self):
        assert classify_mode("I have a new project to start", None) == "advisor"

    def test_advisor_new_goal(self):
        assert classify_mode("Adding a new goal this quarter", None) == "advisor"

    def test_advisor_new_habit(self):
        assert classify_mode("I want to build a new habit", None) == "advisor"

    def test_advisor_soft_conflicts(self):
        """Soft conflicts with no other signals → advisor."""
        conflicts = [{"severity": "soft", "conflict_type": "commitment_overload"}]
        assert classify_mode("I need to figure things out", conflicts) == "advisor"

    def test_advisor_multiple_soft_conflicts(self):
        conflicts = [
            {"severity": "soft", "conflict_type": "commitment_overload"},
            {"severity": "soft", "conflict_type": "goal_contradiction"},
        ]
        assert classify_mode("I need to figure things out", conflicts) == "advisor"

    # ── Case insensitivity ──

    def test_case_insensitive_should_i(self):
        assert classify_mode("SHOULD I quit?", None) == "dialectic"

    def test_case_insensitive_advisor(self):
        assert classify_mode("I WANT TO START a new routine", None) == "advisor"

    def test_case_insensitive_guardian(self):
        conflicts = [{"severity": "hard"}]
        assert classify_mode("GOING TO skip training", conflicts) == "guardian"

    # ── Performance ──

    def test_classification_is_fast(self):
        """Classification must run in <1ms (pure string matching)."""
        start = time.perf_counter()
        for _ in range(1000):
            classify_mode("Should I quit my job and start a new project?", [
                {"severity": "soft", "conflict_type": "commitment_overload"},
            ])
        elapsed_ms = (time.perf_counter() - start) * 1000
        # 1000 calls should finish in <100ms (i.e., <0.1ms each on average)
        assert elapsed_ms < 100, f"1000 classifications took {elapsed_ms:.1f}ms — too slow"
