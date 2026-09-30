import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from extract_repo_skills.contracts import ValidationError, read_json
from extract_repo_skills.git_evidence import verify_git_evidence
from test_contracts import CATALOG_PATH, make_report


class GitEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test Contributor")
        self.git("config", "user.email", "contributor@example.invalid")
        self.write("style.css", ".card {\n  display: block;\n  color: red;\n}\n")
        self.write("README.md", "Unchanged documentation\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Baseline")
        self.root_commit = self.git("rev-parse", "HEAD")
        self.write("style.css", ".card {\n  display: block;\n  color: blue;\n}\n@media (max-width: 40rem) {\n  .card { width: 100%; }\n}\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Adapt card layout")
        self.commit = self.git("rev-parse", "HEAD")
        self.catalog = read_json(CATALOG_PATH)
        self.report = make_report(self.catalog, self.commit)
        self.evidence = self.report["assessments"][0]["evidence"][0]

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.repo), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
            text=True, stderr=subprocess.PIPE,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        ).strip()

    def write(self, path, content):
        (self.repo / path).write_text(content, encoding="utf-8")

    def test_added_lines_and_removed_lines(self):
        self.assertEqual(verify_git_evidence(self.report, self.catalog, self.repo), 1)
        self.evidence.update(side="before", start_line=3, end_line=3)
        self.assertEqual(verify_git_evidence(self.report, self.catalog, self.repo), 1)

    def test_unchanged_context_and_missing_files_are_rejected(self):
        for path, start, end in (("style.css", 2, 2), ("style.css", 3, 7), ("README.md", 1, 1), ("missing.css", 1, 1)):
            with self.subTest(path=path, start=start):
                self.evidence.update(path=path, start_line=start, end_line=end)
                with self.assertRaises(ValidationError):
                    verify_git_evidence(self.report, self.catalog, self.repo)

    def test_another_author_is_rejected(self):
        self.report["contributor_email"] = "someone-else@example.invalid"
        with self.assertRaisesRegex(ValidationError, "author does not match"):
            verify_git_evidence(self.report, self.catalog, self.repo)

    def test_coauthorship_requires_review(self):
        self.git("commit", "--amend", "-q", "-m", "Layout\n\nCo-authored-by: Other <other@example.invalid>")
        self.evidence["commit"] = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(ValidationError, "coauthor attribution"):
            verify_git_evidence(self.report, self.catalog, self.repo)

    def test_merge_requires_review(self):
        merge = self.git("commit-tree", "HEAD^{tree}", "-p", self.commit, "-p", self.root_commit, "-m", "Merge")
        self.evidence["commit"] = merge
        with self.assertRaisesRegex(ValidationError, "merge attribution"):
            verify_git_evidence(self.report, self.catalog, self.repo)

    def test_root_commit(self):
        self.evidence.update(commit=self.root_commit, start_line=1, end_line=4)
        self.assertEqual(verify_git_evidence(self.report, self.catalog, self.repo), 1)

    def test_shallow_boundary_is_not_a_root_commit(self):
        shallow = Path(self.temp.name) / "shallow"
        self.git("clone", "-q", "--depth=1", self.repo.as_uri(), str(shallow))
        with self.assertRaisesRegex(ValidationError, "parent history is missing"):
            verify_git_evidence(self.report, self.catalog, shallow)

    def test_paths_are_literal_and_deleted_files_can_be_cited(self):
        self.evidence.update(path="*.css", start_line=5, end_line=7)
        with self.assertRaises(ValidationError):
            verify_git_evidence(self.report, self.catalog, self.repo)
        self.git("rm", "-q", "style.css")
        self.git("commit", "-q", "-m", "Remove styles")
        self.evidence.update(commit=self.git("rev-parse", "HEAD"), path="style.css", side="before", start_line=1, end_line=7)
        self.assertEqual(verify_git_evidence(self.report, self.catalog, self.repo), 1)

    def test_directory_cannot_be_cited_as_a_file(self):
        (self.repo / "styles").mkdir()
        self.write("styles/layout.css", "body { display: grid; }\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Add layout")
        self.evidence.update(commit=self.git("rev-parse", "HEAD"), path="styles", start_line=1, end_line=1)
        with self.assertRaisesRegex(ValidationError, "must reference a file"):
            verify_git_evidence(self.report, self.catalog, self.repo)

    def test_replacement_objects_do_not_rewrite_evidence(self):
        self.git("replace", self.commit, self.root_commit)
        self.assertEqual(verify_git_evidence(self.report, self.catalog, self.repo), 1)

    def test_diff_driver_is_not_executed(self):
        marker = Path(self.temp.name) / "driver-ran"
        self.git("config", "diff.testdriver.command", f"touch {marker}")
        self.git("config", "diff.testdriver.textconv", f"touch {marker}")
        self.write(".gitattributes", "*.css diff=testdriver\n")
        self.assertEqual(verify_git_evidence(self.report, self.catalog, self.repo), 1)
        self.assertFalse(marker.exists())

    def test_cli_distinguishes_contract_and_git_verification(self):
        path = Path(self.temp.name) / "report.json"
        path.write_text(json.dumps(self.report), encoding="utf-8")
        command = [sys.executable, "-m", "extract_repo_skills", "validate-report", str(path), "--catalog", str(CATALOG_PATH)]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        self.assertFalse(output["git_evidence_verified"])
        self.assertFalse(output["skill_semantics_verified"])
        result = subprocess.run([*command, "--repo", str(self.repo)], capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        self.assertTrue(output["git_evidence_verified"])
        self.assertEqual(output["citations_checked"], 1)
        self.report["assessments"][0]["skill_id"] = "invented.skill"
        path.write_text(json.dumps(self.report), encoding="utf-8")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("unknown skill ID", result.stderr)
