"""Shared Claude client factory — single source of truth for all Claude API access."""
from __future__ import annotations

import logging

import anthropic

logger = logging.getLogger(__name__)


def create_claude_client(api_key: str) -> anthropic.Anthropic | None:
    """Create an Anthropic client with explicit key. Returns None if no key."""
    if not api_key:
        logger.error("No Claude API key — Claude disabled")
        return None

    logger.info("Claude client ready (direct API)")
    return anthropic.Anthropic(api_key=api_key)
