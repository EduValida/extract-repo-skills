#!/usr/bin/env python3
"""
Extract structured graph and hierarchical trees from roadmap.sh.

Combines React Flow nodes/edges (from the official API or cached JSON)
with content markdowns from the `developer-roadmap` submodule.
Outputs an evidence-ready, LLM-digestible catalog format.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from extract_repo_skills.roadmap_extractor import build_hierarchy


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
    except OSError, UnicodeError:
        return {"description": "", "resources": []}

    lines = content.strip().split("\n")
    desc_lines = []
    in_desc = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            in_desc = True
            continue
        if stripped.startswith(("Visit the following resources", "- [@")):
            break
        if in_desc and stripped:
            desc_lines.append(stripped)

    description = " ".join(desc_lines).strip()

    resources = []
    # Pattern: [@type@Title](url)
    for m in re.finditer(r"\[@([a-zA-Z0-9_-]+)@([^\]]+)\]\(([^)]+)\)", content):
        resources.append(
            {
                "type": m.group(1),
                "title": m.group(2).strip(),
                "url": m.group(3).strip(),
            }
        )

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


def _enrich_node(node: dict, content_by_node_id: dict) -> dict:
    content = content_by_node_id.get(node["id"], {"description": "", "resources": []})
    children = [
        _enrich_node(child, content_by_node_id) for child in node.get("subtopics", [])
    ]
    return {
        "id": node["id"],
        "name": node["name"],
        "type": node["type"],
        "description": content.get("description", ""),
        "resources": content.get("resources", []),
        "file_path": content.get("file_path"),
        "subtopics_count": len(children),
        "subtopics": children,
    }


def _flatten(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from _flatten(node.get("subtopics", []))


def extract_roadmap(
    slug: str, input_path: str | None = None, content_dir: Path | None = None
) -> dict:
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

    hierarchy = build_hierarchy(data)
    tree_topics = [_enrich_node(topic, content_by_node_id) for topic in hierarchy]

    # Preserve the main topic-to-topic flow from the original canvas.
    flow_edges = []
    for edge in raw_edges:
        source = nodes_by_id.get(edge.get("source"))
        target = nodes_by_id.get(edge.get("target"))
        if (
            source
            and target
            and source.get("type") == "topic"
            and target.get("type") == "topic"
            and (edge.get("data") or {}).get("edgeStyle") == "solid"
        ):
            flow_edges.append((source["id"], target["id"]))

    root_title = data.get("title", {})
    if isinstance(root_title, dict):
        title_str = root_title.get("page") or root_title.get("card") or slug
    else:
        title_str = str(root_title)

    graph_nodes = []
    graph_edges = []
    for item in tree_topics:
        for node in _flatten([item]):
            graph_nodes.append(
                {
                    "id": node["id"],
                    "name": node["name"],
                    "type": node["type"],
                    "description": node["description"],
                    "resources": node["resources"],
                }
            )
            for child in node.get("subtopics", []):
                graph_edges.append(
                    {
                        "source": child["id"],
                        "target": node["id"],
                        "type": "subtopic_of",
                    }
                )

    for source, target in flow_edges:
        graph_edges.append(
            {
                "source": source,
                "target": target,
                "type": "next_in_flow",
            }
        )

    total_subtopics = sum(
        1 for node in _flatten(tree_topics) if node["type"] != "topic"
    )
    return {
        "metadata": {
            "slug": slug,
            "title": title_str,
            "description": data.get("description", "").replace(
                "@currentYear@", str(datetime.now(UTC).year)
            ),
            "extracted_at": datetime.now(UTC).isoformat(),
            "source_api": f"https://roadmap.sh/api/v1-official-roadmap/{slug}",
            "total_topics": len(tree_topics),
            "total_subtopics": total_subtopics,
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
    meta = result["metadata"]
    print(f"\n🗺️  {meta['title']} ({meta['slug']})")
    print(f"   {meta['description']}")
    print(f"   Topics: {meta['total_topics']} | Subtopics: {meta['total_subtopics']}\n")

    def walk(nodes: list[dict], indent: str = "") -> None:
        for index, node in enumerate(nodes):
            last = index == len(nodes) - 1
            branch = "└── " if last else "├── "
            icon = "📦" if node["type"] in ("topic", "group") else "🔹"
            suffix = f" ({node['subtopics_count']})" if node.get("subtopics") else ""
            print(f"{indent}{branch}{icon} {node['name']}{suffix}")
            walk(node.get("subtopics", []), indent + ("    " if last else "│   "))

    walk(result["tree"]["topics"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract structured roadmap graph and trees from roadmap.sh"
    )
    parser.add_argument(
        "--slug",
        default="python",
        help="Roadmap slug (e.g., python, css, rust, docker)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Extract all roadmaps available in developer-roadmap submodule",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing extracted catalog files when using --all",
    )
    parser.add_argument(
        "--input", help="Optional local JSON file of roadmap.sh API response"
    )
    parser.add_argument(
        "--content-dir", help="Path to developer-roadmap/roadmaps/{slug}/content"
    )
    parser.add_argument(
        "--output",
        help="Output path for generated JSON (default: catalogs/graphs/{slug}.json)",
    )
    parser.add_argument(
        "--list", action="store_true", help="List available roadmaps in the submodule"
    )
    args = parser.parse_args()

    submodule_roadmaps = Path("developer-roadmap/roadmaps")

    if args.list:
        if not submodule_roadmaps.exists():
            print(
                "Submodule developer-roadmap/roadmaps not found. Run git submodule update --init."
            )
            sys.exit(1)
        slugs = sorted([d.name for d in submodule_roadmaps.iterdir() if d.is_dir()])
        print(f"Available roadmaps in submodule ({len(slugs)} total):")
        for s in slugs:
            print(f"  - {s}")
        return

    graphs_dir = Path("catalogs/graphs")
    graphs_dir.mkdir(parents=True, exist_ok=True)

    if args.all:
        import time

        if not submodule_roadmaps.exists():
            print("Submodule developer-roadmap/roadmaps not found.", file=sys.stderr)
            sys.exit(1)

        slugs = sorted([d.name for d in submodule_roadmaps.iterdir() if d.is_dir()])
        print(f"🌐 Batch extracting {len(slugs)} roadmaps to {graphs_dir}...")

        success_count = 0
        skipped_count = 0
        failed = []
        index_entries = []

        for i, slug in enumerate(slugs, 1):
            out_file = graphs_dir / f"{slug}.json"
            if out_file.exists() and not args.force:
                print(f"[{i}/{len(slugs)}] ⏩ Skipping {slug} (already exists)")
                skipped_count += 1
                try:
                    with open(out_file, encoding="utf-8") as f:
                        cached = json.load(f)
                    index_entries.append(cached.get("metadata", {"slug": slug}))
                except OSError, ValueError:
                    index_entries.append({"slug": slug})
                continue

            content_dir = submodule_roadmaps / slug / "content"
            print(
                f"[{i}/{len(slugs)}] 📥 Fetching and processing: {slug}...",
                end=" ",
                flush=True,
            )
            try:
                result = extract_roadmap(slug, content_dir=content_dir)
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
                meta = result.get("metadata", {})
                index_entries.append(meta)
                print(
                    f"✅ OK ({meta.get('total_topics', 0)} topics, {meta.get('total_subtopics', 0)} subtopics)"
                )
                success_count += 1
                time.sleep(0.3)
            except Exception as e:  # noqa: BLE001 - batch reports every roadmap failure
                print(f"❌ Failed: {e}")
                failed.append((slug, str(e)))

        index_file = graphs_dir / "index.json"
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                    "total_catalogs": len(index_entries),
                    "catalogs": sorted(index_entries, key=lambda x: x.get("slug", "")),
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        print("\n🎉 Finished batch extraction!")
        print(
            f"   Success: {success_count} | Skipped: {skipped_count} | Failed: {len(failed)}"
        )
        print(f"   Catalog Index saved to: {index_file}")
        if failed:
            print(f"   Failed slugs: {[s for s, _ in failed]}")
        return

    content_dir = (
        Path(args.content_dir)
        if args.content_dir
        else submodule_roadmaps / args.slug / "content"
    )

    print(f"🚀 Processing roadmap: {args.slug}...")
    try:
        result = extract_roadmap(args.slug, args.input, content_dir)
    except Exception as e:  # noqa: BLE001 - CLI reports the underlying failure
        print(f"❌ Error extracting roadmap '{args.slug}': {e}", file=sys.stderr)
        sys.exit(1)

    print_tree_summary(result)

    output_path = (
        Path(args.output) if args.output else Path(f"catalogs/graphs/{args.slug}.json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n💾 Saved structured catalog to: {output_path}")


if __name__ == "__main__":
    main()
