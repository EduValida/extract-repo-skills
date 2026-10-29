import copy
import json
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from test_contracts import CATALOG_PATH
from test_git_evidence import GitRepositoryFixture

from extract_repo_skills.artifacts import (
    ARTIFACT_NAMES,
    CollectionLimits,
    collect_bundle,
    digest,
)
from extract_repo_skills.contracts import ValidationError, validate_report
from extract_repo_skills.contributions import collect_commit
from extract_repo_skills.git_evidence import verify_git_evidence
from extract_repo_skills.repository import (
    AnalysisError,
    Pygit2Reader,
    open_repository,
    validate_remote,
)
from extract_repo_skills.repository_analysis import (
    RepositoryAnalysisRequest,
    ServiceSettings,
    analyze_repository,
    prepare_repository_analysis,
    validate_repository_assessment,
)


class RepositoryAnalysisTests(GitRepositoryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.email = self.report["contributor_email"]
        self.settings = ServiceSettings(
            local_repositories={"fixture": str(self.repo)},
            catalog_path=str(CATALOG_PATH),
        )
        self.reader = Pygit2Reader(self.repo)
        self.addCleanup(self.reader.close)

    def bundle(self, **options):
        return collect_bundle(
            self.reader,
            "local:fixture",
            options.pop("emails", [self.email]),
            options.pop("ref", "HEAD"),
            options.pop("max_commits", 50),
            options.pop("limits", CollectionLimits()),
        )

    def analyze(self, provider=None, **options):
        return analyze_repository(
            RepositoryAnalysisRequest(
                "local:fixture",
                options.pop("emails", [self.email]),
                options.pop("skill", "css"),
                **options,
            ),
            self.settings,
            Path(self.temp.name),
            provider=provider,
        )

    def test_preview_has_exactly_five_reproducible_artifacts_and_no_worktree_content(
        self,
    ):
        self.write("README.md", "PRIVATE UNCOMMITTED DATA\n")
        self.write("untracked.py", "PRIVATE UNTRACKED DATA\n")
        first = self.analyze(dry_run=True)
        second = self.analyze(dry_run=True)
        self.assertEqual(first, second)
        self.assertEqual(set(first["bundle"]["artifacts"]), set(ARTIFACT_NAMES))
        self.assertNotIn("PRIVATE", json.dumps(first))
        for name, value in first["bundle"]["artifacts"].items():
            self.assertEqual(
                first["bundle"]["manifest"]["artifact_sha256"][name], digest(value)
            )
        payload = json.loads(first["prepared"]["request"]["messages"][1]["content"])
        self.assertEqual(payload["bundle"], first["bundle"])

    def test_diff_citations_match_existing_policy_including_root_and_deletion(self):
        for item in self.bundle()["artifacts"]["git-diff.json"]["commits"]:
            legacy = collect_commit(self.repo, item["commit"], self.email)
            self.assertEqual(item["files"], json.loads(json.dumps(legacy["files"])))
        self.git("rm", "-q", "style.css")
        self.git("commit", "-q", "-m", "Remove stylesheet")
        deleted = self.git("rev-parse", "HEAD")
        files = next(
            item["files"]
            for item in self.bundle()["artifacts"]["git-diff.json"]["commits"]
            if item["commit"] == deleted
        )
        self.assertEqual(files[0]["changed_lines"], {"before": [[1, 7]], "after": []})

    def test_multi_email_attribution_excludes_other_authors_and_other_branches(self):
        self.git("config", "user.email", "work@example.invalid")
        self.write("work.css", "body { display: flex; }\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Work identity")
        work = self.git("rev-parse", "HEAD")
        self.git("config", "user.email", "someone@example.invalid")
        self.write("other.css", "body { display: grid; }\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Another author")
        other = self.git("rev-parse", "HEAD")
        self.git("switch", "-q", "-c", "feature")
        self.git("config", "user.email", self.email)
        self.write("branch.css", "body { color: pink; }\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Unmerged work")
        feature = self.git("rev-parse", "HEAD")
        bundle = self.bundle(emails=[self.email, "work@example.invalid"], ref="main")
        selected = bundle["manifest"]["selection"]["selected"]
        self.assertIn(work, [item["commit"] for item in selected])
        self.assertNotIn(other, [item["commit"] for item in selected])
        self.assertNotIn(feature, [item["commit"] for item in selected])
        prepared = prepare_repository_analysis(bundle, self.catalog, 60_000)
        output = {
            "assessments": [
                {
                    "decision": "catalog_gap",
                    "skill_id": None,
                    "rationale": "Contribution outside this reference subset",
                    "evidence": [
                        {
                            "commit": work,
                            "path": "work.css",
                            "side": "after",
                            "start_line": 1,
                            "end_line": 1,
                        }
                    ],
                }
            ]
        }
        report = validate_repository_assessment(
            output, prepared, self.catalog, self.reader
        )
        self.assertEqual(verify_git_evidence(report, self.catalog, self.repo), 1)

    def test_commit_limit_is_explicit_and_uses_author_date_then_sha(self):
        bundle = self.bundle(max_commits=1)
        selection = bundle["manifest"]["selection"]
        expected = min(
            self.reader.history(self.commit, 100),
            key=lambda item: (-item["author"]["timestamp"], item["commit"]),
        )["commit"]
        self.assertEqual(selection["selected"][0]["commit"], expected)
        self.assertEqual(selection["omitted_by_commit_limit"], 1)
        self.assertFalse(selection["coverage_complete"])

    def test_coauthors_merges_empty_and_binary_commits_have_explicit_exclusions(self):
        self.git("commit", "--allow-empty", "-q", "-m", "Empty")
        empty = self.git("rev-parse", "HEAD")
        (self.repo / "binary.bin").write_bytes(b"\0binary")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Binary")
        binary = self.git("rev-parse", "HEAD")
        self.write("coauthor.css", "body { color: red; }\n")
        self.git("add", ".")
        self.git(
            "commit",
            "-q",
            "-m",
            "Shared\n\nCo-authored-by: Other <other@example.invalid>",
        )
        coauthor = self.git("rev-parse", "HEAD")
        merge = self.git(
            "commit-tree",
            "HEAD^{tree}",
            "-p",
            coauthor,
            "-p",
            self.root_commit,
            "-m",
            "Merge",
        )
        self.git("update-ref", "HEAD", merge)
        selection = self.bundle()["manifest"]["selection"]
        reasons = {item["commit"]: item["reason"] for item in selection["excluded"]}
        self.assertEqual(
            reasons,
            {
                empty: "empty_commit",
                binary: "non_text_file",
                coauthor: "coauthor_requires_review",
                merge: "merge_requires_review",
            },
        )
        self.assertFalse(selection["coverage_complete"])

    def test_symlinks_and_mode_only_changes_require_review(self):
        (self.repo / "link.css").symlink_to("style.css")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Add link")
        symlink = self.git("rev-parse", "HEAD")
        self.git("update-index", "--chmod=+x", "style.css")
        self.git("commit", "-q", "-m", "Mode only")
        mode = self.git("rev-parse", "HEAD")
        reasons = {
            item["commit"]: item["reason"]
            for item in self.bundle()["manifest"]["selection"]["excluded"]
        }
        self.assertEqual(reasons[symlink], "non_regular_file")
        self.assertEqual(reasons[mode], "no_citable_changes")

    def test_language_percentages_use_bytes_and_separate_touched_files(self):
        self.git("config", "user.email", "other@example.invalid")
        (self.repo / "vendor").mkdir()
        self.write("vendor/copied.py", "print('vendored')\n")
        self.write("main.py", "print('application')\n")
        self.write("pyproject.toml", "[project]\nname = 'fixture'\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Project context")
        bundle = self.bundle()
        languages = bundle["artifacts"]["languages.json"]
        project = languages["project"]
        self.assertAlmostEqual(
            sum(item["percentage"] for item in project["languages"]), 100, places=5
        )
        self.assertIn("Python", [item["name"] for item in project["languages"]])
        self.assertNotIn(
            "Python",
            [
                item["name"]
                for item in languages["contributor_touched_snapshot_files"]["languages"]
            ],
        )
        self.assertIn(
            {"path": "vendor/copied.py", "reason": "directory_policy", "bytes": 18},
            languages["excluded"],
        )
        manifests = bundle["artifacts"]["project-context.json"]["manifests"]
        self.assertEqual(manifests[0]["path"], "pyproject.toml")
        self.assertEqual(
            manifests[0]["snapshot"], bundle["manifest"]["reference"]["resolved"]
        )

    def test_docs_only_in_python_project_cannot_cite_unchanged_python(self):
        self.write("main.py", "print('application')\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Application")
        self.git("config", "user.email", "docs@example.invalid")
        self.write("README.md", "Clarified documentation\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Docs")
        bundle = self.bundle(emails=["docs@example.invalid"])
        prepared = prepare_repository_analysis(bundle, self.catalog, 60_000)
        bad = {
            "assessments": [
                {
                    "decision": "catalog_gap",
                    "skill_id": None,
                    "rationale": "Not attributable",
                    "evidence": [
                        {
                            "commit": self.git("rev-parse", "HEAD"),
                            "path": "main.py",
                            "side": "after",
                            "start_line": 1,
                            "end_line": 1,
                        }
                    ],
                }
            ]
        }
        with self.assertRaisesRegex(ValidationError, "changed lines"):
            validate_repository_assessment(bad, prepared, self.catalog, self.reader)

    def test_provider_output_rejects_invented_skills_context_and_out_of_scope_commits(
        self,
    ):
        prepared = prepare_repository_analysis(self.bundle(), self.catalog, 60_000)
        for skill_id, commit, path, line in (
            ("invented.skill", self.commit, "style.css", 5),
            ("css.media-queries", self.commit, "style.css", 2),
            ("css.media-queries", self.commit, "README.md", 1),
            ("css.media-queries", "f" * 40, "style.css", 5),
        ):
            bad = copy.deepcopy({"assessments": self.report["assessments"]})
            bad["assessments"][0]["skill_id"] = skill_id
            bad["assessments"][0]["evidence"][0].update(
                commit=commit, path=path, start_line=line, end_line=line
            )
            with (
                self.subTest(skill=skill_id, commit=commit, line=line),
                self.assertRaises(ValidationError),
            ):
                validate_repository_assessment(bad, prepared, self.catalog, self.reader)

    def test_valid_assessment_records_v2_and_no_eligible_contribution_skips_llm(self):
        provider = Mock(
            return_value=(
                {"assessments": self.report["assessments"]},
                {"provider": "fake"},
            )
        )
        result = self.analyze(provider)
        self.assertEqual(result["status"], "completed")
        validate_report(result["report"], self.catalog)
        self.assertEqual(result["report"]["schema_version"], 2)
        self.assertTrue(result["run"]["git_evidence_verified"])
        provider.reset_mock()
        empty = self.analyze(provider, emails=["absent@example.invalid"])
        self.assertEqual(empty["status"], "no_eligible_contributions")
        self.assertEqual(empty["report"]["assessments"], [])
        provider.assert_not_called()
        validate_report(empty["report"], self.catalog)

    def test_forged_coverage_is_rejected_even_without_citations(self):
        result = self.analyze(Mock(return_value=({"assessments": []}, {})))
        report = result["report"]
        report["selection"].update(
            reachable_commits=1, matching_commits=1, considered_commits=1
        )
        report["selection"]["selected"] = report["selection"]["selected"][:1]
        validate_report(report, self.catalog)
        with self.assertRaisesRegex(ValidationError, "coverage"):
            verify_git_evidence(report, self.catalog, self.repo)

    def test_literal_unicode_paths_and_missing_final_newline_keep_citations(self):
        path = "styles à\t.css"
        self.write(path, "body { display: grid; }")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Literal path")
        commit = self.git("rev-parse", "HEAD")
        bundle = self.bundle()
        contribution = next(
            item
            for item in bundle["artifacts"]["git-diff.json"]["commits"]
            if item["commit"] == commit
        )
        self.assertEqual(contribution["files"][0]["path"], path)
        self.assertEqual(contribution["files"][0]["changed_lines"]["after"], [[1, 1]])
        snippet = next(
            item
            for item in bundle["artifacts"]["contribution-context.json"]["entries"]
            if item["commit"] == commit
        )
        self.assertEqual(snippet["snippets"][0]["text"], "body { display: grid; }")

    def test_limits_fail_before_provider_call_and_never_truncate(self):
        provider = Mock()
        for limits in (
            CollectionLimits(max_input_chars=100),
            CollectionLimits(max_history=1),
            CollectionLimits(max_blob_bytes=5),
            CollectionLimits(max_tree_files=1),
            CollectionLimits(max_changed_files=1),
            CollectionLimits(max_snapshot_bytes=5),
        ):
            with (
                self.subTest(limits=limits),
                self.assertRaises(AnalysisError) as caught,
            ):
                analyze_repository(
                    RepositoryAnalysisRequest("local:fixture", [self.email], "css"),
                    replace(self.settings, limits=limits),
                    Path(self.temp.name),
                    provider=provider,
                )
            self.assertEqual(caught.exception.status, 413)
        provider.assert_not_called()

    def test_shallow_history_and_invalid_ref_are_rejected(self):
        shallow = Path(self.temp.name) / "shallow"
        self.git("clone", "-q", "--depth=1", self.repo.as_uri(), str(shallow))
        with self.assertRaisesRegex(AnalysisError, "complete history"):
            Pygit2Reader(shallow)
        with self.assertRaisesRegex(AnalysisError, "resolve"):
            self.bundle(ref="missing")

    def test_generic_remote_fetch_uses_git_transport_and_works_with_bare_objects(self):
        # Simulate only the network transport using real local Git objects. No
        # forge API or host-specific contribution endpoint participates.
        from extract_repo_skills.repository import git_command

        def transport(root, *args):
            if args[0] == "fetch":
                command = [
                    "git",
                    "-C",
                    str(root),
                    "fetch",
                    "-q",
                    "--no-tags",
                    str(self.repo),
                    "HEAD",
                    "+refs/heads/*:refs/heads/*",
                ]
                subprocess.run(command, check=True, capture_output=True)
                return b""
            return git_command(root, *args)

        with (
            patch("extract_repo_skills.repository.git_command", side_effect=transport),
            open_repository(
                "https://git.example.org/team/project.git",
                {},
                ("git.example.org",),
                Path(self.temp.name),
            ) as reader,
        ):
            remote = collect_bundle(
                reader,
                "https://git.example.org/team/project.git",
                [self.email],
                "HEAD",
                50,
                CollectionLimits(),
            )
        self.assertEqual(
            remote["artifacts"]["git-diff.json"],
            self.bundle()["artifacts"]["git-diff.json"],
        )
        self.assertFalse(
            (Path(self.temp.name) / "repository.git" / "style.css").exists()
        )

    def test_remote_policy_and_request_contract_reject_ambiguous_inputs(self):
        for url in (
            "file:///etc",
            "https://git.example.org/x?token=secret",
            "ssh://-command@git.example.org/x",
            "https://user:secret@git.example.org/x",
            "ext::arbitrary",
            "/tmp/repo",
        ):
            with self.subTest(url=url), self.assertRaises(AnalysisError):
                validate_remote(url, ("git.example.org",))
        self.assertEqual(
            validate_remote(
                "ssh://git@git.example.org/team/project.git", ("git.example.org",)
            ),
            "ssh://git@git.example.org/team/project.git",
        )
        with self.assertRaises(AnalysisError) as caught:
            validate_remote("https://other.example.org/repo.git", ("git.example.org",))
        self.assertEqual(caught.exception.status, 403)
        for emails in ([self.email, self.email], ["Display Name"], []):
            with self.assertRaises(AnalysisError):
                RepositoryAnalysisRequest("local:fixture", emails, "css")
