#!/usr/bin/env python3
"""
Extract structured graph and hierarchical trees from roadmap.sh.

Combines React Flow nodes/edges (from the official API or cached JSON)
with content markdowns from the `developer-roadmap` submodule.
Outputs an evidence-ready, LLM-digestible catalog format.
"""

import argparse
import glob
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def slugify(text: str) -> str:
    """Standard slugify matching roadmap.sh conventions."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return re.sub(r"^-+|-+$", "", text)


def parse_markdown_content(md_path: Path) -> dict:
    """Extract clean title, summary description, and reference links from markdown."""
    try:
        content = md_path.read_text(encoding="utf-8")
    except Exception:
        return {"description": "", "resources": []}

    lines = content.strip().split("\n")
    desc_lines = []
    in_desc = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            in_desc = True
            continue
        if stripped.startswith("Visit the following resources") or stripped.startswith("- [@"):
            break
        if in_desc and stripped:
            desc_lines.append(stripped)

    description = " ".join(desc_lines).strip()

    resources = []
    # Pattern: [@type@Title](url)
    for m in re.finditer(r"\[@([a-zA-Z0-9_-]+)@([^\]]+)\]\(([^)]+)\)", content):
        resources.append({
            "type": m.group(1),
            "title": m.group(2).strip(),
            "url": m.group(3).strip(),
        })

    return {
        "description": description,
        "resources": resources,
    }


def fetch_roadmap_json(slug: str, input_path: str | None = None) -> dict:
    """Fetch roadmap definition from local file or roadmap.sh API."""
    if input_path and os.path.exists(input_path):
        with open(input_path, encoding="utf-8") as f:
            return json.load(f)

    url = f"https://roadmap.sh/api/v1-official-roadmap/{slug}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "extract-repo-skills/0.1.0"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_subtopic_parents(nodes_by_id: dict, edges: list) -> dict[str, str]:
    """
    Associate subtopics to their parent topic using:
    1. Direct React Flow edges
    2. Section containment
    3. Spatial stack alignment (contiguous horizontal or vertical blocks)
    4. Proximity fallback
    """
    topics = {nid: n for nid, n in nodes_by_id.items() if n.get("type") == "topic"}
    subtopics = {nid: n for nid, n in nodes_by_id.items() if n.get("type") == "subtopic"}
    sections = [n for n in nodes_by_id.values() if n.get("type") == "section"]

    parent_of: dict[str, str] = {}

    # 1. Direct edges topic -> subtopic
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in topics and t in subtopics:
            parent_of[t] = s
        elif t in topics and s in subtopics:
            parent_of[s] = t

    # 2. Section containment: assign section to closest topic, then subtopics inside inherit it
    for sec in sections:
        spos = sec.get("position", {})
        sx, sy = spos.get("x", 0), spos.get("y", 0)
        sw = sec.get("width") or sec.get("measured", {}).get("width") or 200
        sh = sec.get("height") or sec.get("measured", {}).get("height") or 150

        inside_subs = []
        for sid, snode in subtopics.items():
            pos = snode.get("position", {})
            px, py = pos.get("x", 0), pos.get("y", 0)
            if sx - 15 <= px <= sx + sw + 15 and sy - 15 <= py <= sy + sh + 15:
                inside_subs.append(sid)

        if not inside_subs:
            continue

        # Find closest topic to the section
        closest_topic = None
        min_dist = float("inf")
        for tid, tnode in topics.items():
            tx = tnode.get("position", {}).get("x", 0)
            ty = tnode.get("position", {}).get("y", 0)
            tw = tnode.get("width", 200)
            dist = math.hypot((sx + sw / 2) - (tx + tw / 2), abs(sy - ty))
            if dist < min_dist:
                min_dist = dist
                closest_topic = tid

        if closest_topic:
            for sid in inside_subs:
                if sid not in parent_of:
                    parent_of[sid] = closest_topic

    # 3. Stack propagation: neighbors in tight visual stacks inherit parent
    changed = True
    while changed:
        changed = False
        for sid, snode in subtopics.items():
            if sid in parent_of:
                continue
            sx, sy = snode.get("position", {}).get("x", 0), snode.get("position", {}).get("y", 0)
            sw, sh = snode.get("width", 150), snode.get("height", 50)

            for mapped_id, tid in list(parent_of.items()):
                onode = subtopics.get(mapped_id)
                if not onode:
                    continue
                ox = onode.get("position", {}).get("x", 0)
                oy = onode.get("position", {}).get("y", 0)
                ow = onode.get("width", 150)
                oh = onode.get("height", 50)

                # Vertical stack
                if abs(sx - ox) <= 20 and (abs(sy - (oy + oh)) <= 25 or abs(oy - (sy + sh)) <= 25):
                    parent_of[sid] = tid
                    changed = True
                    break
                # Horizontal stack
                if abs(sy - oy) <= 20 and (abs(sx - (ox + ow)) <= 25 or abs(ox - (sx + sw)) <= 25):
                    parent_of[sid] = tid
                    changed = True
                    break

    # 4. Spatial proximity fallback
    for sid, snode in subtopics.items():
        if sid in parent_of:
            continue
        sx, sy = snode.get("position", {}).get("x", 0), snode.get("position", {}).get("y", 0)
        best_tid = None
        min_dist = float("inf")
        for tid, tnode in topics.items():
            tx, ty = tnode.get("position", {}).get("x", 0), tnode.get("position", {}).get("y", 0)
            dx = abs(sx - tx)
            dy = sy - ty
            weight_y = 1.0 if dy >= -20 else 3.5
            dist = math.hypot(dx * 1.5, abs(dy) * weight_y)
            if dist < min_dist:
                min_dist = dist
                best_tid = tid
        if best_tid:
            parent_of[sid] = best_tid

    return parent_of


def extract_roadmap(slug: str, input_path: str | None = None, content_dir: Path | None = None) -> dict:
    """Extract complete hierarchy and graph for a given roadmap slug."""
    data = fetch_roadmap_json(slug, input_path)

    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges", [])
    nodes_by_id = {n["id"]: n for n in raw_nodes}

    # Index local markdown content files by nodeId
    content_by_node_id = {}
    if content_dir and content_dir.exists():
        cwd = Path.cwd().resolve()
        for md_file in content_dir.glob("*.md"):
            if "@" in md_file.name:
                node_id = md_file.name.rsplit("@", 1)[-1].replace(".md", "")
                parsed = parse_markdown_content(md_file)
                try:
                    rel_path = str(md_file.resolve().relative_to(cwd))
                except ValueError:
                    rel_path = str(md_file)
                parsed["file_path"] = rel_path
                content_by_node_id[node_id] = parsed

    # Identify main flow (topic-to-topic progression)
    topics = {nid: n for nid, n in nodes_by_id.items() if n.get("type") == "topic"}
    flow_edges = []
    for e in raw_edges:
        s, t = e.get("source"), e.get("target")
        if s in topics and t in topics:
            flow_edges.append((s, t))

    # Resolve subtopic parent relationships
    parent_map = resolve_subtopic_parents(nodes_by_id, raw_edges)

    # Sort topics by flow order or vertical position
    topic_positions = {tid: t.get("position", {}).get("y", 0) for tid, t in topics.items()}
    sorted_topic_ids = sorted(topics.keys(), key=lambda tid: topic_positions.get(tid, 0))

    # Build hierarchical tree
    tree_topics = []
    for tid in sorted_topic_ids:
        tnode = topics[tid]
        tlabel = tnode.get("data", {}).get("label") or tnode.get("id")
        tcontent = content_by_node_id.get(tid, {"description": "", "resources": []})

        child_subtopic_ids = [sid for sid, pid in parent_map.items() if pid == tid]
        child_subtopic_ids.sort(key=lambda sid: nodes_by_id[sid].get("position", {}).get("y", 0))

        subtopics_list = []
        for sid in child_subtopic_ids:
            snode = nodes_by_id[sid]
            slabel = snode.get("data", {}).get("label") or snode.get("id")
            scontent = content_by_node_id.get(sid, {"description": "", "resources": []})
            subtopics_list.append({
                "id": sid,
                "name": slabel,
                "type": "subtopic",
                "description": scontent.get("description", ""),
                "resources": scontent.get("resources", []),
                "file_path": scontent.get("file_path"),
            })

        tree_topics.append({
            "id": tid,
            "name": tlabel,
            "type": "topic",
            "description": tcontent.get("description", ""),
            "resources": tcontent.get("resources", []),
            "file_path": tcontent.get("file_path"),
            "subtopics_count": len(subtopics_list),
            "subtopics": subtopics_list,
        })

    # Build clean semantic graph (nodes and relations)
    graph_nodes = []
    # Title / Root
    root_title = data.get("title", {})
    if isinstance(root_title, dict):
        title_str = root_title.get("page") or root_title.get("card") or slug
    else:
        title_str = str(root_title)

    for item in tree_topics:
        graph_nodes.append({
            "id": item["id"],
            "name": item["name"],
            "type": "topic",
            "description": item["description"],
            "resources": item["resources"],
        })
        for sub in item["subtopics"]:
            graph_nodes.append({
                "id": sub["id"],
                "name": sub["name"],
                "type": "subtopic",
                "description": sub["description"],
                "resources": sub["resources"],
            })

    graph_edges = []
    # 1. Flow edges between topics
    for s, t in flow_edges:
        graph_edges.append({
            "source": s,
            "target": t,
            "type": "next_in_flow",
        })
    # 2. Subtopic-of edges
    for sid, pid in parent_map.items():
        graph_edges.append({
            "source": sid,
            "target": pid,
            "type": "subtopic_of",
        })

    return {
        "metadata": {
            "slug": slug,
            "title": title_str,
            "description": data.get("description", "").replace("@currentYear@", str(datetime.now(timezone.utc).year)),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "source_api": f"https://roadmap.sh/api/v1-official-roadmap/{slug}",
            "total_topics": len(tree_topics),
            "total_subtopics": len(parent_map),
        },
        "tree": {
            "title": title_str,
            "slug": slug,
            "topics": tree_topics,
        },
        "graph": {
            "nodes": graph_nodes,
            "edges": graph_edges,
        },
    }


def print_tree_summary(result: dict) -> None:
    """Print ASCII tree view of the extracted hierarchy."""
    meta = result["metadata"]
    print(f"\n🗺️  {meta['title']} ({meta['slug']})")
    print(f"   {meta['description']}")
    print(f"   Topics: {meta['total_topics']} | Subtopics: {meta['total_subtopics']}\n")

    topics = result["tree"]["topics"]
    for i, t in enumerate(topics):
        is_last_topic = i == len(topics) - 1
        t_prefix = "└── " if is_last_topic else "├── "
        subs = t["subtopics"]
        print(f"{t_prefix}📦 [{t['name']}] (id: {t['id']}) — {len(subs)} subtopicos")

        sub_indent = "    " if is_last_topic else "│   "
        for j, s in enumerate(subs):
            is_last_sub = j == len(subs) - 1
            s_prefix = "└── " if is_last_sub else "├── "
            has_desc = "✓ doc" if s["description"] else "no doc"
            print(f"{sub_indent}{s_prefix}🔹 {s['name']} (id: {s['id']}) [{has_desc}]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract structured roadmap graph and trees from roadmap.sh")
    parser.add_argument("--slug", default="python", help="Roadmap slug (e.g., python, css, rust, docker)")
    parser.add_argument("--input", help="Optional local JSON file of roadmap.sh API response")
    parser.add_argument("--content-dir", help="Path to developer-roadmap/roadmaps/{slug}/content")
    parser.add_argument("--output", help="Output path for generated JSON (default: catalogs/graphs/{slug}.json)")
    parser.add_argument("--list", action="store_true", help="List available roadmaps in the submodule")
    args = parser.parse_args()

    submodule_roadmaps = Path("developer-roadmap/roadmaps")

    if args.list:
        if not submodule_roadmaps.exists():
            print("Submodule developer-roadmap/roadmaps not found. Run git submodule update --init.")
            sys.exit(1)
        slugs = sorted([d.name for d in submodule_roadmaps.iterdir() if d.is_dir()])
        print(f"Available roadmaps in submodule ({len(slugs)} total):")
        for s in slugs:
            print(f"  - {s}")
        return

    content_dir = Path(args.content_dir) if args.content_dir else submodule_roadmaps / args.slug / "content"

    print(f"🚀 Processing roadmap: {args.slug}...")
    try:
        result = extract_roadmap(args.slug, args.input, content_dir)
    except Exception as e:
        print(f"❌ Error extracting roadmap '{args.slug}': {e}", file=sys.stderr)
        sys.exit(1)

    print_tree_summary(result)

    output_path = Path(args.output) if args.output else Path(f"catalogs/graphs/{args.slug}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Saved structured catalog to: {output_path}")


if __name__ == "__main__":
    main()
