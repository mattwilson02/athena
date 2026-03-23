"""Relationship intelligence service — mention tracking, health scoring, and stats persistence."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# Context classification keyword lists (keyword co-occurrence in the same message)
_POSITIVE_CONTEXT: list[str] = [
    "great", "amazing", "love", "fun", "happy", "excited",
    "looking forward", "enjoyed", "good time", "proud of",
]
_NEGATIVE_CONTEXT: list[str] = [
    "stressed", "annoyed", "frustrated", "worried", "argue",
    "conflict", "upset", "angry", "disappointed", "anxious",
    "cancelled", "flaked", "let down",
]
_PLANNING_CONTEXT: list[str] = [
    "plan", "trip", "visit", "meet", "dinner", "call",
    "catch up", "schedule", "going to see", "hanging out",
]

# Expected frequency → days threshold for drift detection
_FREQUENCY_THRESHOLDS: dict[str, int | None] = {
    "daily": 3,
    "weekly": 14,
    "monthly": 45,
    "rare": 120,
    "inactive": None,  # never flag
}

# Simple stop words for topic extraction
_STOP_WORDS = frozenset([
    "a", "an", "the", "and", "but", "or", "for", "nor", "so", "yet",
    "at", "by", "in", "of", "on", "to", "up", "as", "is", "it", "its",
    "was", "be", "been", "are", "am", "were", "with", "i", "me", "my",
    "we", "our", "you", "he", "she", "they", "them", "his", "her",
    "their", "this", "that", "these", "those", "have", "has", "had",
    "will", "would", "could", "should", "may", "might", "do", "did",
    "does", "not", "no", "just", "about", "from", "what", "when",
    "where", "who", "how", "all", "also", "some", "into", "going",
    "got", "get", "like", "said", "tell", "talked", "think", "there",
    "then", "now", "can", "out", "if", "too", "very", "really", "was",
    "been", "being", "had", "has", "have", "did",
])


# ── Helpers ──


def _classify_context(message_lower: str) -> str:
    """Classify message context as negative, positive, planning, or neutral.

    Negative is checked first so stress/conflict signals take precedence.
    """
    if any(kw in message_lower for kw in _NEGATIVE_CONTEXT):
        return "negative"
    if any(kw in message_lower for kw in _POSITIVE_CONTEXT):
        return "positive"
    if any(kw in message_lower for kw in _PLANNING_CONTEXT):
        return "planning"
    return "neutral"


def _extract_topics(message: str, person_title: str) -> list[str]:
    """Extract simple keyword topics from a message, excluding the person's name."""
    person_words = set(person_title.lower().split())
    topics: list[str] = []
    for word in message.lower().split():
        clean = word.strip(".,!?;:\"'()-–—")
        if len(clean) < 3:
            continue
        if clean in _STOP_WORDS:
            continue
        if clean in person_words:
            continue
        topics.append(clean)
        if len(topics) >= 8:
            break
    return topics


def _parse_session_date(timestamp_str: str) -> date | None:
    """Parse a message timestamp string to a date. Returns None on failure."""
    if not timestamp_str:
        return None
    try:
        return datetime.fromisoformat(
            timestamp_str.replace("Z", "+00:00")
        ).date()
    except (ValueError, TypeError):
        return None


# ── Public API ──


def scan_mentions(chat_store, graph, lookback_days: int = 30) -> list[dict]:
    """Scan conversation history to count person mention frequency, recency, and context.

    Scans all sessions from chat_store for user messages within the lookback window
    and checks whether each person node's title/aliases appear.
    Also counts graph_update entries in assistant messages that target person nodes.

    Per-message dedup: a person mentioned N times in one message counts as 1.

    Returns a list of mention stats dicts for every person node in the graph,
    including persons with zero mentions.
    """
    today = date.today()
    cutoff = today - timedelta(days=lookback_days)

    persons = graph.get_nodes_by_type("person")

    # Build per-person title lookup (case-insensitive)
    # Includes the full title, individual name words (for first-name matching),
    # and any aliases defined in frontmatter.
    person_lookup: dict[str, dict] = {}
    for person in persons:
        pid = person["id"]
        titles: set[str] = set()
        title = person.get("title", "")
        if title:
            title_lower = title.lower()
            titles.add(title_lower)
            # Also add individual name words (≥3 chars) for first-name matching
            for word in title_lower.split():
                if len(word) >= 3:
                    titles.add(word)
        # Support aliases in frontmatter
        aliases = person.get("aliases")
        if aliases:
            if isinstance(aliases, list):
                for a in aliases:
                    if a:
                        titles.add(str(a).lower())
            elif isinstance(aliases, str) and aliases:
                titles.add(aliases.lower())
        person_lookup[pid] = {"node": person, "titles": titles}

    person_ids = set(person_lookup.keys())

    # Initialize per-person running stats
    stats: dict[str, dict] = {}
    for pid, info in person_lookup.items():
        stats[pid] = {
            "person_id": pid,
            "person_title": info["node"].get("title", pid),
            "relationship": info["node"].get("relationship"),
            "expected_frequency": info["node"].get("frequency"),
            "mention_count": 0,
            "last_mentioned_date": None,
            "mention_contexts": {"positive": 0, "negative": 0, "planning": 0, "neutral": 0},
            "_topic_words": [],
        }

    # Scan all sessions
    for session_summary in chat_store.list_sessions():
        session_id = session_summary["id"]
        session = chat_store.get_session(session_id)
        if session is None:
            continue

        for message in session.get("messages", []):
            role = message.get("role")
            timestamp_str = message.get("timestamp")

            if not timestamp_str:
                continue  # skip messages without timestamp

            msg_date = _parse_session_date(timestamp_str)
            if msg_date is None:
                continue

            if msg_date < cutoff:
                continue  # outside lookback window

            if role == "user":
                content = message.get("content", "")
                content_lower = content.lower()

                for pid, info in person_lookup.items():
                    # Per-message dedup: check once, count at most +1
                    if not any(
                        t and t in content_lower for t in info["titles"]
                    ):
                        continue

                    stat = stats[pid]
                    stat["mention_count"] += 1
                    if (
                        stat["last_mentioned_date"] is None
                        or msg_date > stat["last_mentioned_date"]
                    ):
                        stat["last_mentioned_date"] = msg_date

                    ctx = _classify_context(content_lower)
                    stat["mention_contexts"][ctx] += 1

                    topics = _extract_topics(content, info["node"].get("title", ""))
                    stat["_topic_words"].extend(topics)

            elif role == "assistant":
                # Graph updates targeting person nodes count as mentions
                graph_updates = message.get("graph_updates") or []
                mentioned_pids: set[str] = set()

                for update in graph_updates:
                    action = update.get("action", "")
                    if action in ("update", "create"):
                        node_id = update.get("node_id", "")
                        if node_id in person_ids:
                            mentioned_pids.add(node_id)
                    elif action == "link":
                        source = update.get("source", "")
                        target = update.get("target", "")
                        if source in person_ids:
                            mentioned_pids.add(source)
                        if target in person_ids:
                            mentioned_pids.add(target)

                for pid in mentioned_pids:
                    stat = stats[pid]
                    stat["mention_count"] += 1
                    if (
                        stat["last_mentioned_date"] is None
                        or msg_date > stat["last_mentioned_date"]
                    ):
                        stat["last_mentioned_date"] = msg_date
                    stat["mention_contexts"]["neutral"] += 1

    # Build final results
    results: list[dict] = []
    for pid, stat in stats.items():
        last_date = stat["last_mentioned_date"]
        last_iso = last_date.isoformat() if last_date else None
        days_since = (today - last_date).days if last_date else None

        # Deduplicate topics, preserve order
        seen: set[str] = set()
        unique_topics: list[str] = []
        for word in stat["_topic_words"]:
            if word not in seen and len(unique_topics) < 5:
                seen.add(word)
                unique_topics.append(word)

        results.append({
            "person_id": pid,
            "person_title": stat["person_title"],
            "relationship": stat["relationship"],
            "expected_frequency": stat["expected_frequency"],
            "mention_count": stat["mention_count"],
            "last_mentioned": last_iso,
            "days_since_mention": days_since,
            "mention_contexts": stat["mention_contexts"],
            "recent_topics": unique_topics,
        })

    return results


def assess_relationship_health(mention_stats: list[dict], today: date) -> list[dict]:
    """Enrich mention stats with health assessment, influence scoring, and context profile.

    Takes the output of scan_mentions() and adds:
    - health: active | drifting | neglected | no_data
    - drift_days: days past expected frequency threshold (max 0 if not drifting)
    - influence_score: 0.0-1.0 relative to most-mentioned person
    - influence_rank: rank by mention count (1 = most mentioned; ties share rank)
    - context_profile: mostly_positive | mostly_negative | mixed | neutral
    """
    if not mention_stats:
        return []

    max_count = max(s["mention_count"] for s in mention_stats)

    # Build influence rank (ties get the same rank)
    sorted_by_count = sorted(
        mention_stats, key=lambda s: s["mention_count"], reverse=True
    )
    rank_map: dict[str, int] = {}
    for i, s in enumerate(sorted_by_count):
        if i == 0 or s["mention_count"] < sorted_by_count[i - 1]["mention_count"]:
            rank_map[s["person_id"]] = i + 1
        else:
            rank_map[s["person_id"]] = rank_map[sorted_by_count[i - 1]["person_id"]]

    results: list[dict] = []
    for stat in mention_stats:
        expected_freq = stat.get("expected_frequency")
        days_since = stat.get("days_since_mention")

        # Determine threshold — missing frequency defaults to monthly
        if expected_freq == "inactive":
            threshold = None
        elif expected_freq is None:
            threshold = _FREQUENCY_THRESHOLDS["monthly"]
        else:
            threshold = _FREQUENCY_THRESHOLDS.get(expected_freq, _FREQUENCY_THRESHOLDS["monthly"])

        # Health assessment
        if threshold is None:
            # inactive — never flag
            health = "no_data"
            drift_days = 0
        elif days_since is None:
            # No mentions in lookback window — can't assess cadence
            health = "no_data"
            drift_days = 0
        elif days_since <= threshold:
            health = "active"
            drift_days = 0
        elif days_since <= threshold * 2:
            health = "drifting"
            drift_days = days_since - threshold
        else:
            health = "neglected"
            drift_days = days_since - threshold

        # Influence score
        influence_score = (stat["mention_count"] / max_count) if max_count > 0 else 0.0
        influence_rank = rank_map[stat["person_id"]]

        # Context profile — based on positive vs negative vs neutral distribution
        ctx = stat.get("mention_contexts", {})
        pos = ctx.get("positive", 0)
        neg = ctx.get("negative", 0)
        neu = ctx.get("neutral", 0)

        if pos > (neg + neu):
            context_profile = "mostly_positive"
        elif neg > (pos + neu):
            context_profile = "mostly_negative"
        elif pos > 0 and neg > 0:
            context_profile = "mixed"
        else:
            context_profile = "neutral"

        result = dict(stat)
        result["health"] = health
        result["drift_days"] = drift_days
        result["influence_score"] = round(influence_score, 4)
        result["influence_rank"] = influence_rank
        result["context_profile"] = context_profile
        results.append(result)

    return results


def persist_mention_stats(vault_service, relationships: list[dict], today: date) -> int:
    """Write computed mention stats back to person node frontmatter.

    Only updates persons with mention_count > 0. Skips unchanged stats.
    Returns the count of nodes updated.
    """
    updated = 0
    graph = getattr(vault_service, "graph", None)

    for rel in relationships:
        if rel["mention_count"] == 0:
            continue

        pid = rel["person_id"]
        patch = {
            "last_mentioned": rel.get("last_mentioned") or str(today),
            "mention_count_30d": rel["mention_count"],
            "mention_profile": rel.get("context_profile", "neutral"),
        }

        # Skip if stats haven't changed
        if graph is not None:
            try:
                existing = graph.get_node(pid)
                if existing is not None and (
                    existing.get("last_mentioned") == patch["last_mentioned"]
                    and existing.get("mention_count_30d") == patch["mention_count_30d"]
                    and existing.get("mention_profile") == patch["mention_profile"]
                ):
                    continue
            except Exception:
                pass  # If comparison fails, proceed with write

        try:
            result = vault_service.update({
                "node_id": pid,
                "changes": {"frontmatter": patch},
            })
            if not result.get("error"):
                updated += 1
            else:
                logger.warning(
                    f"Failed to persist mention stats for {pid}: {result.get('error')}"
                )
        except Exception:
            logger.warning(
                f"Skipping mention stat persistence for {pid} — node may not exist"
            )

    return updated
