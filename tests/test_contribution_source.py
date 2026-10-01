import unittest
from pathlib import Path
from unittest.mock import patch

from extract_repo_skills.contracts import ValidationError
from extract_repo_skills.contribution_source import _github_reference, resolve_contribution
from extract_repo_skills.git_evidence import _git
from test_git_evidence import GitRepositoryFixture


class ContributionSourceTests(GitRepositoryFixture, unittest.TestCase):
    def test_local_commit_resolves_immutable_sha_and_recorded_author(self):
        with resolve_contribution("HEAD", self.repo) as source:
            self.assertEqual(source.commit, self.commit)
            self.assertEqual(source.contributor_email, self.report["contributor_email"])
            self.assertEqual(source.repo, self.repo)
            self.assertEqual(source.repository, self.repo.as_uri())

    def test_single_commit_pr_uses_original_commit_not_merge_commit(self):
        with patch("extract_repo_skills.contribution_source._github_json", return_value=[{"sha": self.commit}]) as api:
            with resolve_contribution("https://github.com/example/project/pull/123", self.repo) as source:
                self.assertEqual(source.commit, self.commit)
                self.assertEqual(source.repository, "https://github.com/example/project")
            api.assert_called_once_with("/repos/example/project/pulls/123/commits?per_page=2")

    def test_multi_commit_pr_is_rejected_before_fetching_or_assessing(self):
        with patch("extract_repo_skills.contribution_source._github_json", return_value=[
            {"sha": self.root_commit}, {"sha": self.commit},
        ]), patch("extract_repo_skills.contribution_source._git") as git:
            with self.assertRaisesRegex(ValidationError, "multiple commits"):
                with resolve_contribution("https://github.com/example/project/pull/123"):
                    self.fail("Unsupported PR must not be yielded")
            git.assert_not_called()

    def test_commit_url_resolves_short_sha_and_preserves_repository(self):
        with patch("extract_repo_skills.contribution_source._github_json", return_value={"sha": self.commit}):
            with resolve_contribution(f"https://github.com/example/project/commit/{self.commit[:7]}", self.repo) as source:
                self.assertEqual(source.commit, self.commit)
                self.assertEqual(source.repository, "https://github.com/example/project")

    def test_url_downloads_commit_and_parent_to_temporary_checkout(self):
        remote = self.repo.as_uri()

        def git(root, *args):
            if "fetch" in args:
                args = (*args[:-2], remote, args[-1])
            return _git(root, *args)

        with patch("extract_repo_skills.contribution_source._github_json", return_value={"sha": self.commit}), patch(
            "extract_repo_skills.contribution_source._git", side_effect=git,
        ):
            with resolve_contribution(f"https://github.com/example/project/commit/{self.commit}") as source:
                temporary = source.repo
                self.assertTrue(temporary.exists())
                self.assertEqual(_git(source.repo, "show", f"{self.commit}:style.css"), (self.repo / "style.css").read_text())
                self.assertEqual(_git(source.repo, "cat-file", "-t", self.root_commit).strip(), "commit")
                self.assertFalse((source.repo / "style.css").exists(), "Fetched source must not be checked out")
            self.assertFalse(temporary.exists())

    def test_rejects_invalid_urls_before_network(self):
        references = [
            "https://example.com/a/b/commit/" + self.commit,
            "https://github.com.evil.invalid/a/b/pull/1",
            "https://user:password@github.com/a/b/pull/1",
            "https://github.com/a/b", "https://github.com/a/b/pull/0",
            "https://github.com/a/b/commit/main", "http://github.com/a/b/pull/1",
        ]
        for reference in references:
            with self.subTest(reference=reference), patch("extract_repo_skills.contribution_source._github_json") as api:
                with self.assertRaises(ValidationError):
                    _github_reference(reference)
                api.assert_not_called()
