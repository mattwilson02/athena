"""Unit tests for _compute_token_budget() — dynamic token budget calculation."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mentor_agent import _compute_token_budget, _BASE_TOKEN_BUDGET, _MAX_TOKEN_BUDGET


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _general_intent(compact=False):
    return {"intent": "general", "k": 5, "compact": compact, "pre_filter": None, "scoring_adjustments": {}}

def _temporal_broad_intent(compact=True):
    return {"intent": "temporal_broad", "k": 15, "compact": compact, "pre_filter": None,
            "scoring_adjustments": {"temporal_boost": 0.5}}

def _relational_intent(compact=True):
    return {"intent": "relational", "k": 5, "compact": compact, "pre_filter": None,
            "scoring_adjustments": {}, "expand_person": True}

def _domain_filter_intent(compact=True):
    return {"intent": "domain_filter", "k": 10, "compact": compact, "pre_filter": None,
            "scoring_adjustments": {}}

def _entity_lookup_intent():
    return {"intent": "entity_lookup", "k": 5, "compact": False, "pre_filter": None,
            "scoring_adjustments": {}}


# ---------------------------------------------------------------------------
# Base and floor tests
# ---------------------------------------------------------------------------

class TestBaseAndFloor:

    def test_base_budget_for_general_intent(self):
        """General intent, 5 nodes, small graph → exactly base budget."""
        result = _compute_token_budget(_general_intent(), selected_count=5, total_nodes=50)
        assert result == _BASE_TOKEN_BUDGET

    def test_floor_at_base(self):
        """Minimal values → never below _BASE_TOKEN_BUDGET."""
        result = _compute_token_budget(_general_intent(), selected_count=0, total_nodes=0)
        assert result >= _BASE_TOKEN_BUDGET

    def test_entity_lookup_unchanged(self):
        """Entity lookup, 5 nodes, small graph → base budget (no scaling)."""
        result = _compute_token_budget(_entity_lookup_intent(), selected_count=5, total_nodes=50)
        assert result == _BASE_TOKEN_BUDGET

    def test_non_compact_no_scaling(self):
        """Non-compact intent with many nodes → no selected_count scaling."""
        result_few = _compute_token_budget(_general_intent(compact=False), selected_count=5, total_nodes=50)
        result_many = _compute_token_budget(_general_intent(compact=False), selected_count=15, total_nodes=50)
        assert result_few == result_many == _BASE_TOKEN_BUDGET


# ---------------------------------------------------------------------------
# Scaling tests
# ---------------------------------------------------------------------------

class TestScaling:

    def test_temporal_broad_scales_up(self):
        """Temporal broad with compact=True and 12 nodes → budget > base."""
        result = _compute_token_budget(_temporal_broad_intent(), selected_count=12, total_nodes=50)
        assert result > _BASE_TOKEN_BUDGET

    def test_relational_scales_up(self):
        """Relational intent with compact=True and 10 nodes → budget > base."""
        result = _compute_token_budget(_relational_intent(), selected_count=10, total_nodes=50)
        assert result > _BASE_TOKEN_BUDGET

    def test_domain_filter_slight_increase(self):
        """Domain filter intent → budget slightly above base (1.2x multiplier)."""
        result = _compute_token_budget(_domain_filter_intent(), selected_count=5, total_nodes=50)
        assert result >= _BASE_TOKEN_BUDGET
        # With 5 nodes (no compact scaling) and 1.2x multiplier: 3000 * 1.2 = 3600
        assert result == 3600

    def test_compact_scaling_adds_300_per_extra_node(self):
        """compact=True, selected_count=8 → adds (8-5)*300=900 tokens before multiplier."""
        result = _compute_token_budget(_domain_filter_intent(compact=True), selected_count=8, total_nodes=50)
        # base=3000 + 3*300=900 → 3900, then *1.2 = 4680
        assert result == 4680

    def test_large_graph_bonus_over_100(self):
        """Graph with 150 nodes → 10% higher than same query on 50-node graph."""
        result_small = _compute_token_budget(_general_intent(), selected_count=5, total_nodes=50)
        result_large = _compute_token_budget(_general_intent(), selected_count=5, total_nodes=150)
        assert result_large > result_small

    def test_large_graph_bonus_over_200(self):
        """Graph with 250 nodes → 20% higher than small graph baseline."""
        result_small = _compute_token_budget(_general_intent(), selected_count=5, total_nodes=50)
        result_large = _compute_token_budget(_general_intent(), selected_count=5, total_nodes=250)
        assert result_large > result_small
        assert result_large == int(_BASE_TOKEN_BUDGET * 1.2)

    def test_large_graph_bonus_distinguishes_100_vs_200(self):
        """101-node graph gets less bonus than 201-node graph."""
        result_100 = _compute_token_budget(_general_intent(), selected_count=5, total_nodes=101)
        result_200 = _compute_token_budget(_general_intent(), selected_count=5, total_nodes=201)
        assert result_200 > result_100


# ---------------------------------------------------------------------------
# Cap tests
# ---------------------------------------------------------------------------

class TestCap:

    def test_cap_at_max(self):
        """Extreme values → capped at _MAX_TOKEN_BUDGET."""
        result = _compute_token_budget(
            _temporal_broad_intent(), selected_count=100, total_nodes=300
        )
        assert result == _MAX_TOKEN_BUDGET

    def test_cap_never_exceeds_8000(self):
        """Any combination of inputs → never exceeds 8000 tokens."""
        for selected in (0, 5, 15, 50, 100):
            for nodes in (0, 50, 150, 300):
                for intent in (_temporal_broad_intent(), _relational_intent(), _domain_filter_intent()):
                    result = _compute_token_budget(intent, selected, nodes)
                    assert result <= _MAX_TOKEN_BUDGET, (
                        f"Budget {result} exceeded max for selected={selected}, nodes={nodes}"
                    )

    def test_floor_never_below_3000(self):
        """Any inputs → budget never below _BASE_TOKEN_BUDGET."""
        for selected in (0, 1, 5):
            for nodes in (0, 5, 50):
                result = _compute_token_budget(_general_intent(), selected, nodes)
                assert result >= _BASE_TOKEN_BUDGET
