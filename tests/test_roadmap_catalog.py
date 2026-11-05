import copy
import json
import tempfile
import unittest
from pathlib import Path

from extract_repo_skills.contracts import ValidationError
from extract_repo_skills.roadmap_catalog import (
    build_roadmap,
    iter_leaves,
    load_roadmap,
    validate_roadmap,
    write_roadmaps,
)


def graph() -> dict:
    return {
        "metadata": {
            "slug": "example",
            "title": "Example Roadmap",
            "description": "metadata must disappear",
        },
        "tree": {
            "title": "Example Roadmap",
            "slug": "example",
            "topics": [
                {
                    "id": "topic-1",
                    "name": "Introduction",
                    "type": "topic",
                    "description": "description must disappear",
                    "resources": [{"title": "resource", "url": "https://example.com"}],
                    "file_path": "content/introduction.md",
                    "subtopics_count": 2,
                    "subtopics": [
                        {"id": "sub-1", "name": "Client", "type": "subtopic"},
                        {"id": "sub-2", "name": "Client", "type": "subtopic"},
                    ],
                },
                {
                    "id": "topic-2",
                    "name": "Empty Topic",
                    "type": "topic",
                    "subtopics": [],
                },
            ],
        },
        "graph": {"nodes": [], "edges": []},
    }


class RoadmapCatalogTests(unittest.TestCase):
    def test_builds_concise_schema_and_drops_provenance(self):
        roadmap = build_roadmap(graph())
        self.assertEqual(
            roadmap,
            {
                "title": "Example Roadmap",
                "topics": [
                    {
                        "title": "Introduction",
                        "importance": 50,
                        "completion_threshold": 60,
                        "children": [
                            {"title": "Client"},
                            {"title": "Client (2)"},
                        ],
                    },
                    {"title": "Empty Topic"},
                ],
            },
        )
        self.assertNotIn("metadata", json.dumps(roadmap))
        self.assertNotIn("description", json.dumps(roadmap))
        self.assertNotIn("id", json.dumps(roadmap))

    def test_build_is_deterministic(self):
        self.assertEqual(build_roadmap(graph()), build_roadmap(copy.deepcopy(graph())))

    def test_validate_rejects_unknown_fields_and_bad_thresholds(self):
        roadmap = build_roadmap(graph())
        validate_roadmap(roadmap)
        roadmap["topics"][0]["description"] = "unexpected"
        with self.assertRaisesRegex(ValidationError, "unexpected"):
            validate_roadmap(roadmap)
        roadmap = build_roadmap(graph())
        roadmap["topics"][0]["importance"] = 101
        with self.assertRaisesRegex(ValidationError, "importance"):
            validate_roadmap(roadmap)
        roadmap = build_roadmap(graph())
        roadmap["topics"][1]["title"] = "Introduction"
        with self.assertRaisesRegex(ValidationError, "duplicate sibling"):
            validate_roadmap(roadmap)

    def test_iter_leaves_returns_paths(self):
        roadmap = build_roadmap(graph())
        leaves = list(iter_leaves(roadmap))
        self.assertEqual(
            [path for _, path in leaves],
            [
                ("Introduction", "Client"),
                ("Introduction", "Client (2)"),
                ("Empty Topic",),
            ],
        )

    def test_load_by_title_casefold_and_slug(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            roadmap = build_roadmap(graph())
            (root / "example.json").write_text(json.dumps(roadmap), encoding="utf-8")
            for skill in ("Example Roadmap", "example roadmap", "example"):
                with self.subTest(skill=skill):
                    loaded = load_roadmap(skill, root)
                    self.assertEqual(loaded["slug"], "example")
                    self.assertEqual(loaded["roadmap"], roadmap)
            self.assertIsNone(load_roadmap("Unknown Roadmap", root))

    def test_writer_skips_existing_files_unless_forced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            graphs = root / "graphs"
            output = root / "roadmaps"
            graphs.mkdir()
            (graphs / "example.json").write_text(json.dumps(graph()), encoding="utf-8")
            written = write_roadmaps(graphs, output)
            self.assertEqual(len(written), 1)
            first = (output / "example.json").read_text(encoding="utf-8")
            self.assertEqual(write_roadmaps(graphs, output), [])
            self.assertEqual(
                (output / "example.json").read_text(encoding="utf-8"), first
            )
            self.assertEqual(len(write_roadmaps(graphs, output, force=True)), 1)


if __name__ == "__main__":
    unittest.main()
