"""Reconstruct a readable hierarchy from the roadmap.sh React Flow canvas.

The public API exposes nodes, edges, sections, labels and positions. It does not
expose parent IDs. This module combines explicit dashed edges with visual
sections and group labels, using positions only as a fallback.
"""

from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

MAX_COLUMN_DISTANCE = 180
MAX_GROUP_GAP = 80
MAX_SECTION_GAP = 24
TEXT_MATCH_THRESHOLD = 0.45
FOOTER_PREFIXES = (
    "find the detailed version",
    "visit the following",
    "continue learning",
    "have a look at",
)
GENERIC_TOKENS = {
    "advanced",
    "application",
    "architecture",
    "basic",
    "data",
    "introduction",
    "network",
    "networking",
    "principle",
    "protocol",
    "system",
    "tool",
    "type",
}


def _label(node: dict) -> str:
    data = node.get("data") or {}
    return (data.get("label") or data.get("name") or "").strip()


def _style(node: dict) -> dict:
    return (node.get("data") or {}).get("style") or {}


def _size(node: dict) -> tuple[float, float]:
    measured = node.get("measured") or {}
    return (
        measured.get("width") or node.get("width") or 0,
        measured.get("height") or node.get("height") or 0,
    )


def _center(node: dict) -> tuple[float, float]:
    position = node.get("position") or {}
    x, y = position.get("x", 0), position.get("y", 0)
    width, height = _size(node)
    return x + width / 2, y + height / 2


def _inside(node: dict, section: dict) -> bool:
    x, y = _center(node)
    position = section.get("position") or {}
    sx, sy = position.get("x", 0), position.get("y", 0)
    width, height = _size(section)
    return sx <= x <= sx + width and sy <= y <= sy + height


def _is_heading(node: dict) -> bool:
    kind = node.get("type")
    label = _label(node)
    if not label:
        return False
    if kind == "label":
        return True
    if kind == "paragraph":
        if label.casefold().startswith(FOOTER_PREFIXES):
            return False
        return (_style(node).get("fontSize") or 0) >= 20
    return False


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return {
        word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words
    }


def _text_score(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    if overlap:
        return overlap
    best_pair = max(
        (
            SequenceMatcher(None, source, target).ratio()
            for source in left_tokens
            for target in right_tokens
            if source not in GENERIC_TOKENS and target not in GENERIC_TOKENS
        ),
        default=0.0,
    )
    return best_pair if best_pair >= 0.8 else 0.0


def _best_topic_for_text(
    text: str,
    topics: list[dict],
    fallback: dict,
) -> dict:
    best = max(topics, key=lambda topic: _text_score(text, _label(topic)))
    if _text_score(text, _label(best)) >= TEXT_MATCH_THRESHOLD:
        return best
    return fallback


def _nearest_topic_to_group(
    group_ids: list[str],
    nodes_by_id: dict,
    topics: list[dict],
) -> dict:
    centers = [_center(nodes_by_id[node_id]) for node_id in group_ids]
    x = sum(center[0] for center in centers) / len(centers)
    y = sum(center[1] for center in centers) / len(centers)
    return min(
        topics,
        key=lambda topic: math.hypot(_center(topic)[0] - x, _center(topic)[1] - y),
    )


def _nearest_topic(node: dict, topics: list[dict], *, use_top: bool = False) -> dict:
    x, y = _center(node)
    if use_top:
        y = (node.get("position") or {}).get("y", y)

    def distance(topic: dict) -> float:
        tx, ty = _center(topic)
        if use_top:
            ty = (topic.get("position") or {}).get("y", ty)
        return math.hypot(tx - x, ty - y)

    return min(topics, key=distance)


def _explicit_parents(nodes_by_id: dict, edges: list) -> dict[str, str]:
    parents: dict[str, str] = {}
    for edge in edges:
        if (edge.get("data") or {}).get("edgeStyle") != "dashed":
            continue
        source = nodes_by_id.get(edge.get("source"))
        target = nodes_by_id.get(edge.get("target"))
        if not source or not target:
            continue
        if source.get("type") not in ("topic", "subtopic", "label", "paragraph"):
            continue
        if target.get("type") != "subtopic":
            continue
        parents[target["id"]] = source["id"]
    return parents


def _section_owners(
    sections: list[dict],
    topics: list[dict],
    headings: list[dict],
    nodes_by_id: dict,
    edges: list,
    explicit_parents: dict[str, str],
) -> dict[str, str]:
    explicit_sections: dict[str, str] = {}
    for edge in edges:
        if (edge.get("data") or {}).get("edgeStyle") != "dashed":
            continue
        source = nodes_by_id.get(edge.get("source"))
        target = nodes_by_id.get(edge.get("target"))
        if (
            source
            and target
            and source.get("type") == "topic"
            and target.get("type") == "section"
        ):
            explicit_sections[target["id"]] = source["id"]

    owners: dict[str, str] = {}
    for section in sections:
        if section["id"] in explicit_sections:
            owners[section["id"]] = explicit_sections[section["id"]]
            continue
        inside_headings = [heading for heading in headings if _inside(heading, section)]
        fallback = _nearest_topic(section, topics, use_top=True)
        owner = fallback
        best_score = 0.0
        for heading in inside_headings:
            candidate = max(
                topics, key=lambda topic: _text_score(_label(heading), _label(topic))
            )
            score = _text_score(_label(heading), _label(candidate))
            if score > best_score:
                best_score, owner = score, candidate
        if best_score < TEXT_MATCH_THRESHOLD:
            owner = fallback
        owners[section["id"]] = owner["id"]
    return owners


def _heading_owner(
    heading: dict,
    topics: list[dict],
    sections: list[dict],
    section_owners: dict[str, str],
    fallback: dict,
) -> str:
    containing = next(
        (section for section in sections if _inside(heading, section)), None
    )
    if containing is not None:
        return section_owners[containing["id"]]
    return _best_topic_for_text(_label(heading), topics, fallback)["id"]


def _next_heading_y(
    heading: dict,
    headings: list[dict],
) -> float:
    x = (heading.get("position") or {}).get("x", 0)
    y = (heading.get("position") or {}).get("y", 0)
    candidates = [
        (other.get("position") or {}).get("y", 0)
        for other in headings
        if other is not heading
        and abs((other.get("position") or {}).get("x", 0) - x) <= MAX_COLUMN_DISTANCE
        and (other.get("position") or {}).get("y", 0) > y
    ]
    return min(candidates, default=float("inf"))


def _group_headings(
    headings: list[dict],
    subtopics: list[dict],
    explicit_parents: dict[str, str],
) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    used: set[str] = set()
    for heading in sorted(
        headings,
        key=lambda node: (
            (node.get("position") or {}).get("x", 0),
            (node.get("position") or {}).get("y", 0),
        ),
    ):
        hx = (heading.get("position") or {}).get("x", 0)
        hy = (heading.get("position") or {}).get("y", 0)
        next_y = _next_heading_y(heading, headings)
        candidates = []
        for subtopic in subtopics:
            if subtopic["id"] in used or subtopic["id"] in explicit_parents:
                continue
            sx = (subtopic.get("position") or {}).get("x", 0)
            sy = (subtopic.get("position") or {}).get("y", 0)
            if abs(sx - hx) > MAX_COLUMN_DISTANCE:
                continue
            if not (hy - MAX_SECTION_GAP <= sy < next_y):
                continue
            candidates.append(subtopic)

        candidates.sort(key=lambda node: (node.get("position") or {}).get("y", 0))
        selected = []
        last_y = None
        for candidate in candidates:
            y = (candidate.get("position") or {}).get("y", 0)
            if last_y is not None and y - last_y > MAX_GROUP_GAP:
                break
            selected.append(candidate)
            last_y = y
        if selected:
            groups[heading["id"]] = [node["id"] for node in selected]
            used.update(node["id"] for node in selected)
    return groups


def build_hierarchy(data: dict) -> list[dict]:
    """Return top-level topics with nested groups and subtopics."""
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    nodes_by_id = {node["id"]: node for node in nodes}
    topics = [node for node in nodes if node.get("type") == "topic"]
    subtopics = [node for node in nodes if node.get("type") == "subtopic"]
    sections = [node for node in nodes if node.get("type") == "section"]
    headings = [node for node in nodes if _is_heading(node)]
    explicit_parents = _explicit_parents(nodes_by_id, edges)
    section_owners = _section_owners(
        sections, topics, headings, nodes_by_id, edges, explicit_parents
    )
    groups = _group_headings(headings, subtopics, explicit_parents)

    parents: dict[str, str] = dict(explicit_parents)
    for heading_id, child_ids in groups.items():
        for child_id in child_ids:
            parents[child_id] = heading_id

    active_headings = {
        heading["id"]
        for heading in headings
        if heading["id"] in groups
        or any(parent_id == heading["id"] for parent_id in parents.values())
    }
    for heading in headings:
        if heading["id"] not in active_headings:
            continue
        fallback = _nearest_topic_to_group(
            [heading["id"], *groups.get(heading["id"], [])],
            nodes_by_id,
            topics,
        )
        parents[heading["id"]] = _heading_owner(
            heading, topics, sections, section_owners, fallback
        )

    assigned = set(parents)
    for subtopic in subtopics:
        if subtopic["id"] in assigned:
            continue
        parents[subtopic["id"]] = _nearest_topic(subtopic, topics)["id"]

    children: dict[str, list[str]] = {}
    for child_id, parent_id in parents.items():
        children.setdefault(parent_id, []).append(child_id)

    def sort_key(node_id: str):
        node = nodes_by_id[node_id]
        position = node.get("position") or {}
        return (position.get("y", 0), position.get("x", 0))

    def build(node_id: str, seen: set[str] | None = None) -> dict:
        seen = set(seen or ())
        if node_id in seen:
            raise ValueError(f"cycle detected at {node_id}")
        seen.add(node_id)
        node = nodes_by_id[node_id]
        kind = node.get("type")
        result = {
            "id": node_id,
            "name": _label(node),
            "type": "group" if node_id in active_headings else kind,
            "subtopics": [
                build(child_id, seen)
                for child_id in sorted(children.get(node_id, []), key=sort_key)
            ],
        }
        return result

    hierarchy = [
        build(topic["id"])
        for topic in sorted(topics, key=lambda topic: sort_key(topic["id"]))
    ]
    return hierarchy
