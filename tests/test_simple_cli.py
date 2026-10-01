import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from extract_repo_skills.cli import DEFAULT_CATALOG, main
from extract_repo_skills.contracts import read_json, validate_report
from test_contracts import CATALOG_PATH
from test_git_evidence import GitRepositoryFixture


class SimpleCLITests(GitRepositoryFixture, unittest.TestCase):
    def arguments(self, *extra):
        return ["extract-repo-skills", "analyze", self.commit, "--repo", str(self.repo), *extra]

    def test_default_catalog_matches_the_reviewable_catalog(self):
        self.assertEqual(read_json(DEFAULT_CATALOG), read_json(CATALOG_PATH))

    def test_dry_run_infers_author_and_uses_default_catalog_without_llm(self):
        with patch("sys.argv", self.arguments("--dry-run")), patch(
            "extract_repo_skills.deepseek_provider.assess",
        ) as provider, redirect_stdout(io.StringIO()) as stdout:
            main()
        prepared = json.loads(stdout.getvalue())
        self.assertEqual(prepared["report_context"]["contributor_email"], self.report["contributor_email"])
        self.assertEqual(prepared["commit"], self.commit)
        self.assertEqual(prepared["report_context"]["catalog"]["id"], "roadmap-starter")
        provider.assert_not_called()

    def test_summary_and_automatic_output_are_backed_by_a_valid_report(self):
        output = {"assessments": self.report["assessments"]}
        cwd = os.getcwd()
        try:
            os.chdir(self.temp.name)
            with patch("sys.argv", self.arguments()), patch(
                "extract_repo_skills.deepseek_provider.assess", return_value=(output, {}),
            ), redirect_stdout(io.StringIO()) as stdout:
                main()
            paths = list(Path("reports").glob("*.json"))
            reports = [path for path in paths if not path.name.endswith(".run.json")]
            self.assertEqual(len(reports), 1)
            report = read_json(reports[0])
            validate_report(report, self.catalog)
            run = read_json(Path(str(reports[0]) + ".run.json"))
            self.assertEqual(run["contribution"], self.commit)
            self.assertIn("CSS media queries", stdout.getvalue())
            self.assertIn("Human review required", stdout.getvalue())
        finally:
            os.chdir(cwd)

    def test_author_override_cannot_attribute_another_persons_commit(self):
        with patch("sys.argv", self.arguments("--contributor-email", "someone-else@example.invalid")), patch(
            "extract_repo_skills.deepseek_provider.assess",
        ) as provider, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main()
            provider.assert_not_called()
