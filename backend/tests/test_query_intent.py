"""Tests for classify_query_intent() — query intent classification."""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mentor_agent import classify_query_intent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intent(query, date_range=None, domains=None):
    return classify_query_intent(query, date_range, domains or [])


THIS_WEEK = (date(2026, 3, 17), date(2026, 3, 23))  # 6-day range (days >= 3)
TOMORROW = (date(2026, 3, 24), date(2026, 3, 24))   # 0-day range


# ---------------------------------------------------------------------------
# temporal_broad
# ---------------------------------------------------------------------------

class TestTemporalBroadIntent:

    def test_temporal_broad_this_week(self):
        result = _intent("what happened this week", date_range=THIS_WEEK)
        assert result["intent"] == "temporal_broad"

    def test_temporal_broad_summary_signal_no_date(self):
        """Summary signal without date range → temporal_broad with k=10."""
        result = _intent("give me a summary of what I've been working on")
        assert result["intent"] == "temporal_broad"
        assert result["k"] == 10

    def test_temporal_broad_what_did_i_do(self):
        result = _intent("what did I do this week", date_range=THIS_WEEK)
        assert result["intent"] == "temporal_broad"
        assert result["k"] == 15

    def test_temporal_broad_compact_true(self):
        result = _intent("overview of this week", date_range=THIS_WEEK)
        assert result["compact"] is True

    def test_temporal_broad_recap_signal(self):
        result = _intent("can you give me a recap")
        assert result["intent"] == "temporal_broad"

    def test_temporal_broad_has_pre_filter(self):
        """Broad query with date range should exclude resolved statuses."""
        result = _intent("what happened this week", date_range=THIS_WEEK)
        assert result["pre_filter"] is not None

    def test_temporal_broad_scoring_adjustments(self):
        result = _intent("what happened this week", date_range=THIS_WEEK)
        adj = result["scoring_adjustments"]
        assert adj.get("temporal_boost", 0) > 0.3  # higher than default
        assert adj.get("permanence_multiplier", 1.0) < 1.0  # reduced


# ---------------------------------------------------------------------------
# temporal_specific
# ---------------------------------------------------------------------------

class TestTemporalSpecificIntent:

    def test_temporal_specific_tomorrow(self):
        result = _intent("what's on tomorrow", date_range=TOMORROW)
        assert result["intent"] == "temporal_specific"

    def test_temporal_specific_k_10(self):
        result = _intent("what's on for today", date_range=(date(2026, 3, 23), date(2026, 3, 23)))
        assert result["intent"] == "temporal_specific"
        assert result["k"] == 10

    def test_temporal_specific_not_compact(self):
        result = _intent("plans for tomorrow", date_range=TOMORROW)
        assert result["compact"] is False

    def test_temporal_specific_recency_boost(self):
        result = _intent("plans for tomorrow", date_range=TOMORROW)
        adj = result["scoring_adjustments"]
        assert adj.get("recency_multiplier", 1.0) > 1.0


# ---------------------------------------------------------------------------
# domain_filter
# ---------------------------------------------------------------------------

class TestDomainFilterIntent:

    def test_domain_filter_show_goals(self):
        result = _intent("show me my goals", domains=["Self"])
        assert result["intent"] == "domain_filter"

    def test_domain_filter_k_10(self):
        result = _intent("list my habits", domains=["Self"])
        assert result["k"] == 10

    def test_domain_filter_compact(self):
        result = _intent("show me my goals", domains=["Self"])
        assert result["compact"] is True

    def test_domain_filter_needs_dominance(self):
        """Evenly split domains — no dominant domain → not domain_filter."""
        # Use a query without filter signal phrases that has even domain coverage
        # "goal task" hits Self (goal) and Planning (task) equally — no dominant domain
        result = _intent("thinking about my goal and my task today", domains=["Self", "Planning"])
        # Without dominant domain (needs 3+ hits and 2x lead), should NOT be domain_filter
        # The classifier checks is_domain_dominant which requires top_score >= 3 and >= 2x second
        assert result["intent"] != "domain_filter"

    def test_domain_filter_scoring_adjustments(self):
        result = _intent("show me my goals", domains=["Self"])
        adj = result["scoring_adjustments"]
        assert adj.get("domain_boost", 0.2) > 0.2  # boosted


# ---------------------------------------------------------------------------
# entity_lookup
# ---------------------------------------------------------------------------

class TestEntityLookupIntent:

    def test_entity_lookup_short_query(self):
        """Short query with no temporal/domain signals → entity_lookup."""
        result = _intent("Sarah")
        assert result["intent"] == "entity_lookup"

    def test_entity_lookup_tell_me_about(self):
        result = _intent("tell me about my fear of failure")
        assert result["intent"] == "entity_lookup"

    def test_entity_lookup_k_5(self):
        result = _intent("tell me about Sarah")
        assert result["k"] == 5

    def test_entity_lookup_not_compact(self):
        result = _intent("tell me about Sarah")
        assert result["compact"] is False

    def test_entity_lookup_centrality_boost(self):
        result = _intent("what is stoicism")
        assert result["intent"] == "entity_lookup"
        adj = result["scoring_adjustments"]
        assert adj.get("centrality_multiplier", 1.0) > 1.0


# ---------------------------------------------------------------------------
# general
# ---------------------------------------------------------------------------

class TestGeneralIntent:

    _GENERAL_QUERY = "hey there, I was just thinking about various random things and wanted to chat"

    def test_general_default(self):
        result = _intent(self._GENERAL_QUERY)
        assert result["intent"] == "general"

    def test_general_k_5(self):
        result = _intent(self._GENERAL_QUERY)
        assert result["k"] == 5

    def test_general_not_compact(self):
        result = _intent(self._GENERAL_QUERY)
        assert result["compact"] is False

    def test_general_no_adjustments(self):
        result = _intent(self._GENERAL_QUERY)
        assert result["scoring_adjustments"] == {}


# ---------------------------------------------------------------------------
# Priority ordering and edge cases
# ---------------------------------------------------------------------------

class TestIntentPriority:

    def test_temporal_broad_beats_domain(self):
        """'What did I work on this month' has temporal + domain signals — temporal wins."""
        result = _intent("what did I work on this month", date_range=(date(2026, 3, 1), date(2026, 3, 31)))
        assert result["intent"] == "temporal_broad"

    def test_empty_query_general(self):
        result = _intent("")
        assert result["intent"] == "general"

    def test_empty_query_k_5(self):
        result = _intent("")
        assert result["k"] == 5


# ---------------------------------------------------------------------------
# k values and compact flag correctness
# ---------------------------------------------------------------------------

class TestIntentOutputShape:

    _GENERAL_LONG = "hey there, I was just thinking about various random things and wanted to chat"

    def test_k_values_match_intent(self):
        """Verify k values: broad=15, specific=10, domain=10, entity=5, general=5."""
        assert _intent("what happened this week", date_range=THIS_WEEK)["k"] == 15
        assert _intent("what's on tomorrow", date_range=TOMORROW)["k"] == 10
        assert _intent("show me my goals", domains=["Self"])["k"] == 10
        assert _intent("tell me about Sarah")["k"] == 5
        assert _intent(self._GENERAL_LONG)["k"] == 5

    def test_compact_flag_correct(self):
        """temporal_broad and domain_filter → compact=True; others → compact=False."""
        assert _intent("what happened this week", date_range=THIS_WEEK)["compact"] is True
        assert _intent("show me my goals", domains=["Self"])["compact"] is True
        assert _intent("what's on tomorrow", date_range=TOMORROW)["compact"] is False
        assert _intent("tell me about Sarah")["compact"] is False
        assert _intent(self._GENERAL_LONG)["compact"] is False

    def test_result_has_required_keys(self):
        result = _intent("test query")
        assert "intent" in result
        assert "k" in result
        assert "compact" in result
        assert "pre_filter" in result
        assert "scoring_adjustments" in result


# ---------------------------------------------------------------------------
# relational intent (Task 1)
# ---------------------------------------------------------------------------

class TestRelationalIntent:

    def test_relational_explicit_signal(self):
        """Explicit relational signal phrase → relational intent."""
        result = _intent("how's my relationship with ben")
        assert result["intent"] == "relational"

    def test_relational_entity_plus_people_domain(self):
        """Entity signal + People domain in top domains → relational."""
        result = _intent("tell me about sarah", domains=["People"])
        assert result["intent"] == "relational"

    def test_relational_who_is_person(self):
        """'who is' + People domain → relational."""
        result = _intent("who is ethan", domains=["People"])
        assert result["intent"] == "relational"

    def test_relational_does_not_match_non_person(self):
        """Entity signal without People domain → entity_lookup, not relational."""
        result = _intent("tell me about piano", domains=["Knowledge"])
        assert result["intent"] == "entity_lookup"

    def test_relational_loses_to_temporal(self):
        """Temporal signal + date range takes priority over relational."""
        result = _intent("what did i do with ben this week", date_range=THIS_WEEK)
        assert result["intent"] == "temporal_broad"

    def test_relational_expand_person_flag(self):
        """Relational intent includes expand_person=True."""
        result = _intent("how's my relationship with ben")
        assert result.get("expand_person") is True

    def test_relational_k_and_compact(self):
        """Relational intent has k=5 and compact=True."""
        result = _intent("dynamics with my team")
        assert result["k"] == 5
        assert result["compact"] is True

    def test_relational_dynamics_with(self):
        """'dynamics with' phrase triggers relational."""
        result = _intent("dynamics with my team")
        assert result["intent"] == "relational"

    def test_relational_history_with(self):
        """'history with' phrase triggers relational."""
        result = _intent("history with my old mentor")
        assert result["intent"] == "relational"

    def test_relational_scoring_adjustments(self):
        """Relational intent has centrality and session multipliers."""
        result = _intent("how's my relationship with ben")
        adj = result["scoring_adjustments"]
        assert adj.get("centrality_multiplier", 1.0) >= 2.0
        assert adj.get("session_multiplier", 1.0) >= 1.0
