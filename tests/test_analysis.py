import copy
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from extract_repo_skills.analysis import prepare_analysis, validate_assessment
from extract_repo_skills.cli import main
from extract_repo_skills.contracts import ValidationError, validate_report
from extract_repo_skills.contributions import collect_commit
from test_contracts import CATALOG_PATH
from test_git_evidence import GitRepositoryFixture


class AnalysisTests(GitRepositoryFixture, unittest.TestCase):
    def prepare(self, revision="HEAD", **kwargs):
        return prepare_analysis(self.repo, revision, self.report["contributor_email"], self.catalog, **kwargs)

    def test_collects_committed_changes_and_context_only(self):
        self.write("style.css", "This uncommitted text must never be sent\n")
        self.write("private.txt", "Untracked content must never be sent\n")
        prepared = self.prepare()
        contribution = json.loads(prepared["request"]["messages"][1]["content"])["contribution"]
        self.assertEqual(contribution["commit"], self.commit)
        self.assertEqual([item["path"] for item in contribution["files"]], ["style.css"])
        file = contribution["files"][0]
        self.assertIn("  display: block;", file["patch"])
        self.assertIn("+@media", file["patch"])
        self.assertEqual(file["changed_lines"]["after"], [[3, 6]])
        self.assertNotIn("uncommitted", prepared["request"]["messages"][1]["content"])
        self.assertNotIn("Untracked", prepared["request"]["messages"][1]["content"])

    def test_root_and_deleted_file_collection(self):
        root = self.prepare(self.root_commit)
        self.assertEqual(root["commit"], self.root_commit)
        self.git("rm", "-q", "style.css")
        self.git("commit", "-q", "-m", "Remove stylesheet")
        contribution = json.loads(self.prepare()["request"]["messages"][1]["content"])["contribution"]
        self.assertEqual(contribution["files"][0]["changed_lines"]["before"], [[1, 7]])
        self.assertEqual(contribution["files"][0]["changed_lines"]["after"], [])

    def test_preflight_limits_and_authorship(self):
        with self.assertRaisesRegex(ValidationError, "exceeds"):
            self.prepare(max_input_chars=50)
        with self.assertRaisesRegex(ValidationError, "author does not match"):
            collect_commit(self.repo, "HEAD", "another@example.invalid")
        with self.assertRaisesRegex(ValidationError, "limit is 1"):
            collect_commit(self.repo, self.root_commit, self.report["contributor_email"], max_files=1)

    def test_empty_and_binary_commits_need_review(self):
        self.git("commit", "--allow-empty", "-q", "-m", "No changes")
        with self.assertRaisesRegex(ValidationError, "no changes"):
            self.prepare()
        (self.repo / "image.bin").write_bytes(b"\x00\xff\x00")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Binary content")
        with self.assertRaisesRegex(ValidationError, "no citable text"):
            self.prepare()

    def test_non_regular_files_need_review(self):
        (self.repo / "linked.css").symlink_to("style.css")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Add symlink")
        with self.assertRaisesRegex(ValidationError, "non-regular"):
            self.prepare()

    def test_model_output_is_validated_against_git_and_catalog(self):
        prepared = self.prepare()
        output = {"assessments": self.report["assessments"]}
        result = validate_assessment(output, prepared, self.catalog, self.repo)
        validate_report(result, self.catalog)
        for skill_id, commit, line in (
            ("invented.skill", self.commit, 5),
            ("css.media-queries", self.root_commit, 1),
            ("css.media-queries", self.commit, 2),
        ):
            with self.subTest(skill_id=skill_id, commit=commit, line=line):
                bad = copy.deepcopy(output)
                bad["assessments"][0]["skill_id"] = skill_id
                bad["assessments"][0]["evidence"][0].update(commit=commit, start_line=line, end_line=line)
                with self.assertRaises(ValidationError):
                    validate_assessment(bad, prepared, self.catalog, self.repo)

    def test_dry_run_needs_no_credentials_and_makes_no_provider_call(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "DEEPSEEK_MODEL": ""}):
            result = subprocess.run(
                [sys.executable, "-m", "extract_repo_skills", "analyze-commit",
                 "--repo", str(self.repo), "--commit", "HEAD", "--contributor-email",
                 self.report["contributor_email"], "--catalog", str(CATALOG_PATH), "--dry-run"],
                capture_output=True, text=True, check=True,
            )
        self.assertEqual(json.loads(result.stdout)["commit"], self.commit)

    def test_cli_accepts_valid_assessment_and_records_run(self):
        destination = Path(self.temp.name) / "assessment.json"
        arguments = [
            "extract-repo-skills", "analyze-commit", "--repo", str(self.repo),
            "--commit", "HEAD", "--contributor-email", self.report["contributor_email"],
            "--catalog", str(CATALOG_PATH), "--model", "test-model", "--output", str(destination),
        ]
        output = {"assessments": self.report["assessments"]}
        with patch("sys.argv", arguments), patch(
            "extract_repo_skills.deepseek_provider.assess", return_value=(output, {"human_review_required": True})
        ) as provider, redirect_stderr(io.StringIO()):
            main()
            self.assertEqual(provider.call_count, 1)
            report = json.loads(destination.read_text())
            validate_report(report, self.catalog)
            run = json.loads(Path(str(destination) + ".run.json").read_text())
            self.assertTrue(run["git_evidence_verified"])
            with self.assertRaises(SystemExit):
                main()
            self.assertEqual(provider.call_count, 1, "Existing output must be rejected before another API call")

    def test_cli_never_writes_invalid_model_output(self):
        destination = Path(self.temp.name) / "invalid.json"
        arguments = [
            "extract-repo-skills", "analyze-commit", "--repo", str(self.repo),
            "--commit", "HEAD", "--contributor-email", self.report["contributor_email"],
            "--catalog", str(CATALOG_PATH), "--model", "test-model", "--output", str(destination),
        ]
        bad = copy.deepcopy(self.report["assessments"])
        bad[0]["skill_id"] = "invented.skill"
        with patch("sys.argv", arguments), patch(
            "extract_repo_skills.deepseek_provider.assess", return_value=({"assessments": bad}, {})
        ), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()) as stdout:
            with self.assertRaises(SystemExit):
                main()
            self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(destination.exists())
        self.assertFalse(Path(str(destination) + ".run.json").exists())
