"""Resolve an explicit contribution to local Git objects for evidence validation."""

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .contracts import ValidationError
from .git_evidence import _git


@dataclass(frozen=True)
class ContributionSource:
    repo: Path
    commit: str
    contributor_email: str
    repository: str
    reference: str


def _github_json(path: str):
    request = Request(
        "https://api.github.com" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "extract-repo-skills"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise ValidationError(
            f"GitHub returned HTTP {exc.code}. URL mode requires a public contribution; "
            "check the URL or use its commit SHA with --repo."
        ) from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ValidationError("Could not read the contribution from GitHub") from exc


def _github_reference(reference: str) -> tuple[str, str]:
    try:
        url = urlparse(reference)
    except ValueError as exc:
        raise ValidationError("Invalid contribution URL") from exc
    if url.scheme != "https" or url.netloc != "github.com":
        raise ValidationError("Use an HTTPS github.com commit or pull request URL")
    match = re.fullmatch(
        r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(commit|pull)/([A-Za-z0-9]+)/?", url.path,
    )
    if not match:
        raise ValidationError("Expected https://github.com/OWNER/REPO/commit/SHA or /pull/NUMBER")
    owner, name, kind, identifier = match.groups()
    repository = f"https://github.com/{owner}/{name}"
    if kind == "commit":
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", identifier):
            raise ValidationError("Commit URL must contain a commit SHA")
        result = _github_json(f"/repos/{owner}/{name}/commits/{identifier}")
        commit = result.get("sha") if isinstance(result, dict) else None
    else:
        if not re.fullmatch(r"[1-9][0-9]*", identifier):
            raise ValidationError("Pull request number must be positive")
        # Fetch at most two entries to distinguish the supported single-commit case.
        commits = _github_json(f"/repos/{owner}/{name}/pulls/{identifier}/commits?per_page=2")
        if not isinstance(commits, list) or not commits:
            raise ValidationError("GitHub returned no commits for the pull request")
        if len(commits) != 1:
            raise ValidationError(
                "PRs with multiple commits require per-commit attribution; "
                "analyze each commit URL separately for now"
            )
        commit = commits[0].get("sha") if isinstance(commits[0], dict) else None
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValidationError("GitHub returned an invalid commit SHA")
    return repository, commit


def _local_source(repo: Path, revision: str, repository: str | None, reference: str) -> ContributionSource:
    root = Path(_git(repo, "rev-parse", "--show-toplevel").strip())
    commit = _git(root, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").strip()
    author = _git(root, "show", "-s", "--format=%ae", commit, "--").strip()
    if not author:
        raise ValidationError("The contribution has no recorded author email")
    return ContributionSource(root, commit, author, repository or root.as_uri(), reference)


@contextmanager
def resolve_contribution(reference: str, repo: str | Path | None = None):
    """Keep a temporary checkout alive until the assessment and verification finish."""
    if "://" not in reference:
        yield _local_source(Path(repo or "."), reference, None, reference)
        return
    repository, commit = _github_reference(reference)
    if repo is not None:
        yield _local_source(Path(repo), commit, repository, reference)
        return
    with TemporaryDirectory(prefix="extract-repo-skills-contribution-") as directory:
        root = Path(directory)
        options = ["-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never"]
        _git(root, *options, "init", "-q")
        # Retrieve the selected commit and parent snapshot; do not check out or run code.
        _git(root, *options, "fetch", "--quiet", "--depth=2", "--no-tags", "--no-recurse-submodules", repository + ".git", commit)
        yield _local_source(root, commit, repository, reference)
