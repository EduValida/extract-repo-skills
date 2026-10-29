import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from test_contracts import CATALOG_PATH
from test_git_evidence import GitRepositoryFixture

from extract_repo_skills.artifacts import CollectionLimits, collect_bundle
from extract_repo_skills.repository import AnalysisError, Pygit2Reader
from extract_repo_skills.repository_analysis import (
    RepositoryAnalysisRequest,
    ServiceSettings,
    analyze_repository,
)
from extract_repo_skills.static_metrics import (
    _functions,
    _run_cloc,
    cloc_snapshot,
    lizard_change,
)


class StaticMetricsTests(unittest.TestCase):
    def test_cloc_counts_comments_blank_code_and_keeps_duplicate_files(self):
        source = "# comment\n\ndef f():\n    return 1\n"
        inputs = {
            "a.py": ("a" * 40, source),
            "copy.py": ("b" * 40, source),
            "ignored.unknown_extension": ("c" * 40, "unknown content"),
        }
        first = cloc_snapshot(inputs, {"a.py"})
        second = cloc_snapshot(inputs, {"a.py"})
        self.assertEqual(
            first, second, "Timing and temporary paths must not enter the artifact"
        )
        self.assertEqual(first["project"]["total_code_lines"], 4)
        self.assertEqual(
            first["contributor_touched_snapshot_files"]["total_code_lines"], 2
        )
        self.assertEqual(
            first["project"]["languages"],
            [
                {
                    "name": "Python",
                    "files": 2,
                    "code": 4,
                    "comment": 2,
                    "blank": 2,
                    "percentage_of_code_lines": 100.0,
                }
            ],
        )
        self.assertEqual(
            first["not_counted"],
            [{"path": "ignored.unknown_extension", "reason": "not_counted_by_cloc"}],
        )
        self.assertEqual(
            [item["blob"] for item in first["files"]], ["a" * 40, "b" * 40]
        )
        self.assertNotIn("elapsed_seconds", json.dumps(first))

    def test_cloc_handles_unusual_names_and_preserves_original_path(self):
        result = cloc_snapshot({'../escape\t à".py': ("a" * 40, "print(1)\n")}, set())
        self.assertEqual(result["files"][0]["path"], '../escape\t à".py')
        self.assertEqual(result["files"][0]["language"], "Python")
        self.assertEqual(result["files"][0]["code"], 1)

    def test_cloc_empty_or_unrecognized_inputs_are_explicit(self):
        for inputs in (
            {},
            {"unknown.unrecognized": ("a" * 40, "unrecognized content")},
        ):
            with self.subTest(inputs=inputs):
                result = cloc_snapshot(inputs, set())
                self.assertEqual(result["status"], "no_applicable_files")
                self.assertEqual(result["project"]["total_code_lines"], 0)

    def test_cloc_is_required_and_timeouts_or_invalid_results_fail_cleanly(self):
        with self.assertRaises(AnalysisError) as caught:
            cloc_snapshot({}, set(), executable="/missing/cloc")
        self.assertEqual(caught.exception.status, 503)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "extract_repo_skills.static_metrics.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("cloc", 30),
                ),
                self.assertRaises(AnalysisError) as caught,
            ):
                _run_cloc("cloc", Path(directory), "--version")
            self.assertEqual(caught.exception.status, 504)
        with (
            patch(
                "extract_repo_skills.static_metrics._run_cloc",
                side_effect=["2.10\n", "PRIVATE BAD JSON"],
            ),
            self.assertRaises(AnalysisError) as caught,
        ):
            cloc_snapshot({"a.py": ("a" * 40, "print(1)\n")}, set())
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_cloc_scratch_files_live_in_and_leave_the_owned_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            cloc_snapshot(
                {"a.py": ("a" * 40, "print(1)\n")}, set(), workspace=workspace
            )
            self.assertEqual(list(workspace.iterdir()), [])

    def test_lizard_pairs_affected_function_and_keeps_unmodified_baseline_side(self):
        before = "def f(x):\n    return x\n\ndef untouched():\n    return 0\n"
        after = "def f(x):\n    if x:\n        return 1\n    return x\n\ndef untouched():\n    return 0\n"
        metrics = lizard_change(
            "a.py",
            {"snapshot": "a" * 40, "blob": "b" * 40, "text": before},
            {"snapshot": "c" * 40, "blob": "d" * 40, "text": after},
            {"before": [], "after": [[2, 3]]},
        )
        self.assertEqual(metrics["status"], "ok")
        self.assertEqual(
            [item["name"] for item in metrics["before"]["functions"]], ["f"]
        )
        self.assertEqual(
            [item["name"] for item in metrics["after"]["functions"]], ["f"]
        )
        self.assertEqual(metrics["before"]["functions"][0]["changed_lines"], [])
        self.assertEqual(metrics["after"]["functions"][0]["changed_lines"], [[2, 3]])
        self.assertEqual(metrics["comparisons"][0]["delta"]["cyclomatic_complexity"], 1)
        self.assertEqual(metrics["comparisons"][0]["status"], "paired_by_unique_name")

    def test_lizard_added_deleted_unsupported_and_no_function_results_are_distinct(
        self,
    ):
        source = {
            "snapshot": "a" * 40,
            "blob": "b" * 40,
            "text": "def f(x):\n    return x\n",
        }
        added = lizard_change("a.py", None, source, {"before": [], "after": [[1, 2]]})
        self.assertEqual(added["before"]["status"], "file_absent")
        self.assertEqual(added["comparisons"][0]["status"], "unmatched_after")
        self.assertIsNone(added["comparisons"][0]["delta"])
        removed = lizard_change("a.py", source, None, {"before": [[1, 2]], "after": []})
        self.assertEqual(removed["after"]["status"], "file_absent")
        self.assertEqual(removed["comparisons"][0]["status"], "unmatched_before")
        unsupported = lizard_change(
            "a.md", None, source, {"before": [], "after": [[1, 2]]}
        )
        self.assertEqual(unsupported["status"], "not_applicable")
        self.assertEqual(unsupported["after"]["status"], "not_applicable")
        global_only = lizard_change(
            "a.py",
            None,
            {**source, "text": "x = 1\n"},
            {"before": [], "after": [[1, 1]]},
        )
        self.assertEqual(global_only["status"], "no_changed_functions_detected")
        self.assertEqual(global_only["after"]["status"], "no_functions_detected")

    def test_lizard_ambiguous_names_never_produce_a_delta(self):
        source = {
            "snapshot": "a" * 40,
            "blob": "b" * 40,
            "text": "def f():\n    return 1\n\ndef f():\n    return 2\n",
        }
        metrics = lizard_change(
            "a.py", source, source, {"before": [[1, 2]], "after": [[1, 2]]}
        )
        self.assertEqual(metrics["comparisons"][0]["status"], "ambiguous")
        self.assertIsNone(metrics["comparisons"][0]["delta"])

    def test_lizard_source_directives_do_not_suppress_functions_or_metrics(self):
        text = "# GENERATED CODE\ndef f(x):\n    #lizard forgive\n    if x:\n        return 1\n    return 0\n"
        functions = _functions("a.py", text)
        self.assertEqual(len(functions), 1)
        self.assertEqual(functions[0]["start_line"], 2)
        self.assertEqual(functions[0]["end_line"], 6)
        self.assertEqual(functions[0]["cyclomatic_complexity"], 2)

    def test_lizard_partial_analysis_diagnostics_reject_the_result(self):
        def partial(*_args):
            print("PRIVATE SOURCE", file=sys.stderr)
            return Mock(function_list=[])

        with patch(
            "extract_repo_skills.static_metrics.lizard.FileAnalyzer"
        ) as analyzer:
            analyzer.return_value.analyze_source_code.side_effect = partial
            with self.assertRaises(AnalysisError) as caught:
                _functions("a.py", "def f(): pass\n")
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn("PRIVATE", str(caught.exception))


class MetricsIntegrationTests(GitRepositoryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.email = self.report["contributor_email"]
        self.settings = ServiceSettings(
            local_repositories={"fixture": str(self.repo)},
            catalog_path=str(CATALOG_PATH),
        )
        self.reader = Pygit2Reader(self.repo)
        self.addCleanup(self.reader.close)

    def analyze(self, provider=None, **options):
        return analyze_repository(
            RepositoryAnalysisRequest(
                "local:fixture",
                [self.email],
                options.pop("skill", "css"),
                **options,
            ),
            self.settings,
            Path(self.temp.name),
            provider=provider,
        )

    def bundle(self, *, limits):
        return collect_bundle(
            self.reader, "local:fixture", [self.email], "HEAD", 50, limits
        )

    def test_metrics_are_included_in_five_artifacts_and_the_exact_llm_input(self):
        self.write("a.py", "def f(x):\n    return x\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Add function")
        self.write("a.py", "def f(x):\n    if x:\n        return 1\n    return x\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Add branch")
        result = self.analyze(dry_run=True)
        artifacts = result["bundle"]["artifacts"]
        self.assertEqual(len(artifacts), 5)
        self.assertEqual(artifacts["languages.json"]["line_counts"]["tool"], "cloc")
        metrics = artifacts["contribution-context.json"]["function_metrics"]
        self.assertEqual(metrics["tool"], "lizard")
        selected = next(
            item
            for item in metrics["files"]
            if item["commit"] == self.git("rev-parse", "HEAD")
            and item["path"] == "a.py"
        )
        self.assertEqual(
            selected["comparisons"][0]["delta"]["cyclomatic_complexity"], 1
        )
        sent = json.loads(result["prepared"]["request"]["messages"][1]["content"])
        self.assertEqual(sent["bundle"]["artifacts"], artifacts)

    def test_missing_cloc_fails_before_llm_call(self):
        provider = Mock()
        self.settings = replace(self.settings, cloc_executable="/missing/cloc")
        with self.assertRaises(AnalysisError) as caught:
            self.analyze(provider)
        self.assertEqual(caught.exception.status, 503)
        provider.assert_not_called()

    def test_input_budget_includes_static_metrics(self):
        limits = CollectionLimits(max_input_chars=200)
        with self.assertRaises(AnalysisError) as caught:
            self.bundle(limits=limits)
        self.assertEqual(caught.exception.status, 413)
