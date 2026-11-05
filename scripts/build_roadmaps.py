#!/usr/bin/env python3
"""Build concise roadmaps from the extracted roadmap.sh graphs."""

import argparse
from pathlib import Path

from extract_repo_skills.roadmap_catalog import (
    DEFAULT_COMPLETION_THRESHOLD,
    DEFAULT_IMPORTANCE,
    write_roadmaps,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graphs-dir", default="catalogs/graphs")
    parser.add_argument("--output-dir", default="catalogs/roadmaps")
    parser.add_argument("--importance", type=int, default=DEFAULT_IMPORTANCE)
    parser.add_argument(
        "--completion-threshold",
        type=int,
        default=DEFAULT_COMPLETION_THRESHOLD,
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    graphs_dir = Path(args.graphs_dir)
    if not graphs_dir.exists():
        raise SystemExit(f"Graph directory not found: {graphs_dir}")
    written = write_roadmaps(
        graphs_dir,
        Path(args.output_dir),
        importance=args.importance,
        completion_threshold=args.completion_threshold,
        force=args.force,
    )
    print(f"Roadmaps written: {len(written)}")
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
