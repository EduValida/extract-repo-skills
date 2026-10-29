import copy
import json
import unittest

from extract_repo_skills.analysis import prepare_analysis, validate_assessment
from extract_repo_skills.contracts import ValidationError, validate_report
from extract_repo_skills.contributions import collect_commit
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

    def test_docs_only_commit_in_rust_repository_keeps_rust_as_a_candidate_not_evidence(self):
        (self.repo / "src").mkdir()
        self.write("src/main.rs", 'fn main() { println!("hello"); }\n')
        self.write("Cargo.toml", '[package]\nname = "rust-demo"\nversion = "0.1.0"\n')
        self.write("README.md", "A Rust project.\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Add Rust application")
        self.write("README.md", "A Rust project.\nPlease report documentation typos in an issue.\n")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "Clarify documentation feedback")

        prepared = self.prepare()
        payload = json.loads(prepared["request"]["messages"][1]["content"])
        self.assertIn("rust.programming", [skill["id"] for skill in payload["catalog"]["skills"]])
        self.assertEqual([file["path"] for file in payload["contribution"]["files"]], ["README.md"])
        self.assertNotIn("fn main", prepared["request"]["messages"][1]["content"])
        expected = {"assessments": [{
            "decision": "insufficient_evidence", "skill_id": "rust.programming",
            "rationale": "The change adds prose about reporting typos; it demonstrates no Rust-language behavior.",
            "evidence": [{
                "commit": prepared["commit"], "path": "README.md", "side": "after",
                "start_line": 2, "end_line": 2,
            }],
        }]}
        # This checks the evaluation plumbing, not whether an actual LLM abstains.
        report = validate_assessment(expected, prepared, self.catalog, self.repo)
        self.assertEqual(report["assessments"], expected["assessments"])

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
