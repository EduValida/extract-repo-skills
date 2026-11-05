"""Simple, version-free roadmaps derived from extracted roadmap.sh trees."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .contracts import _require, _text, read_json

DEFAULT_IMPORTANCE = 50
DEFAULT_COMPLETION_THRESHOLD = 60
DEFAULT_ROADMAPS_DIR = Path("catalogs/roadmaps")
MAX_DEPTH = 10


def _unique_title(title: str, used: set[str]) -> str:
    title = title.strip() or "Topic"
    if title not in used:
        used.add(title)
        return title
    index = 2
    while f"{title} ({index})" in used:
        index += 1
    unique = f"{title} ({index})"
    used.add(unique)
    return unique


def _convert_node(
    raw: dict,
    sibling_titles: set[str],
    importance: int,
    completion_threshold: int,
) -> dict:
    _require(isinstance(raw, dict), "graph.tree.topics: expected an object")
    title = _unique_title(raw.get("name", ""), sibling_titles)
    node = {"title": title}
    subtopics = raw.get("subtopics") or []
    _require(isinstance(subtopics, list), "graph.tree.subtopics: expected an array")
    if subtopics:
        node["importance"] = importance
        node["completion_threshold"] = completion_threshold
        child_titles: set[str] = set()
        node["children"] = [
            _convert_node(child, child_titles, importance, completion_threshold)
            for child in subtopics
        ]
    return node


def build_roadmap(
    graph: dict,
    *,
    importance: int = DEFAULT_IMPORTANCE,
    completion_threshold: int = DEFAULT_COMPLETION_THRESHOLD,
) -> dict:
    """Convert an extracted graph document into the concise roadmap schema."""
    _require(isinstance(graph, dict), "graph: expected an object")
    tree = graph.get("tree")
    _require(isinstance(tree, dict), "graph.tree: expected an object")
    topics = tree.get("topics")
    _require(
        isinstance(topics, list) and bool(topics),
        "graph.tree.topics: expected a nonempty array",
    )
    metadata = graph.get("metadata") or {}
    title = metadata.get("title") or tree.get("title") or "Roadmap"
    sibling_titles: set[str] = set()
    roadmap = {
        "title": title,
        "topics": [
            _convert_node(topic, sibling_titles, importance, completion_threshold)
            for topic in topics
        ],
    }
    validate_roadmap(roadmap)
    return roadmap


def _validate_nodes(nodes: object, context: str, depth: int = 1) -> None:
    _require(
        isinstance(nodes, list) and bool(nodes), f"{context}: expected a nonempty array"
    )
    _require(depth <= MAX_DEPTH, f"{context}: maximum depth is {MAX_DEPTH}")
    titles: set[str] = set()
    for index, node in enumerate(nodes):
        node_context = f"{context}[{index}]"
        _require(isinstance(node, dict), f"{node_context}: expected an object")
        extra = set(node) - {"title", "importance", "completion_threshold", "children"}
        _require(not extra, f"{node_context}: unexpected {sorted(extra)}")
        _require("title" in node, f"{node_context}: missing title")
        _text(node["title"], f"{node_context}.title")
        _require(
            node["title"] not in titles, f"{node_context}: duplicate sibling title"
        )
        titles.add(node["title"])
        for key in ("importance", "completion_threshold"):
            if key in node:
                value = node[key]
                _require(
                    type(value) is int and 0 <= value <= 100,
                    f"{node_context}.{key}: expected an integer from 0 to 100",
                )
        if "children" in node:
            _validate_nodes(node["children"], f"{node_context}.children", depth + 1)


def validate_roadmap(roadmap: dict) -> dict:
    """Validate the concise roadmap schema and return it unchanged."""
    _require(isinstance(roadmap, dict), "roadmap: expected an object")
    _require(
        set(roadmap) == {"title", "topics"},
        "roadmap: expected exactly title and topics",
    )
    _text(roadmap["title"], "roadmap.title")
    _validate_nodes(roadmap["topics"], "roadmap.topics")
    return roadmap


def iter_leaves(roadmap: dict):
    """Yield leaf nodes and their title paths."""
    validate_roadmap(roadmap)

    def walk(nodes: list[dict], path: tuple[str, ...] = ()):
        for node in nodes:
            current = (*path, node["title"])
            if "children" in node:
                yield from walk(node["children"], current)
            else:
                yield node, current

    yield from walk(roadmap["topics"])


def _safe_slug(skill: str) -> str | None:
    value = skill.strip().lower().replace("_", "-")
    if re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", value):
        return value
    return None


def load_roadmap(skill: str, directory: Path = DEFAULT_ROADMAPS_DIR) -> dict | None:
    """Load a roadmap by title, or by slug as a fallback."""
    value = skill.strip()
    if not value:
        return None

    slug = _safe_slug(value)
    if slug is not None:
        path = directory / f"{slug}.json"
        if path.exists():
            roadmap = read_json(path)
            validate_roadmap(roadmap)
            return {"slug": slug, "path": path, "roadmap": roadmap}

    exact = None
    folded = None
    for path in sorted(directory.glob("*.json")):
        if path.name == "index.json":
            continue
        roadmap = read_json(path)
        validate_roadmap(roadmap)
        title = roadmap["title"]
        if title == value and exact is None:
            exact = {"slug": path.stem, "path": path, "roadmap": roadmap}
        if title.casefold() == value.casefold() and folded is None:
            folded = {"slug": path.stem, "path": path, "roadmap": roadmap}
    return exact or folded


def write_roadmaps(
    graphs_dir: Path,
    output_dir: Path,
    *,
    importance: int = DEFAULT_IMPORTANCE,
    completion_threshold: int = DEFAULT_COMPLETION_THRESHOLD,
    force: bool = False,
) -> list[Path]:
    """Write concise roadmaps for every graph document."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for graph_path in sorted(graphs_dir.glob("*.json")):
        if graph_path.name == "index.json":
            continue
        output_path = output_dir / graph_path.name
        if output_path.exists() and not force:
            continue
        graph = read_json(graph_path)
        roadmap = build_roadmap(
            graph,
            importance=importance,
            completion_threshold=completion_threshold,
        )
        output_path.write_text(
            json.dumps(roadmap, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(output_path)
    return written
