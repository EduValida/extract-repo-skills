"""Check citation provenance without executing repository code or diff drivers."""

import re
import subprocess
from pathlib import Path

from .contracts import ValidationError, validate_report


DIFF_OPTIONS = [
    "--no-ext-diff", "--no-textconv", "--no-renames", "--inter-hunk-context=0",
    "--no-color", "--diff-algorithm=myers", "--no-indent-heuristic",
    "--ignore-submodules=none",
]


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-pager", "--no-replace-objects", "--literal-pathspecs", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationError(f"Cannot read Git evidence: {exc}") from exc
    if result.returncode:
        raise ValidationError(f"Cannot read Git evidence: {result.stderr.strip()}")
    return result.stdout


def commit_metadata(root: Path, commit: str, contributor_email: str) -> dict:
    """Check attribution before either collecting or verifying evidence."""
    parts = _git(root, "rev-list", "--parents", "-n", "1", commit, "--").split()
    if not parts or parts[0] != commit:
        raise ValidationError(f"{commit}: evidence must identify a commit")
    parents = parts[1:]
    if len(parents) > 1:
        raise ValidationError(f"{commit}: merge attribution requires review")
    metadata = _git(root, "show", "-s", "--format=%ae%n%B", commit, "--")
    author, _, message = metadata.partition("\n")
    if author != contributor_email:
        raise ValidationError(f"{commit}: author does not match contributor_email")
    if re.search(r"^\s*co-authored-by\s*:", message, re.IGNORECASE | re.MULTILINE):
        raise ValidationError(f"{commit}: coauthor attribution requires review")
    if not parents:
        raw = _git(root, "cat-file", "-p", commit).split("\n\n", 1)[0]
        if any(line.startswith("parent ") for line in raw.splitlines()):
            raise ValidationError(f"{commit}: parent history is missing; use a complete checkout")
    return {"commit": commit, "parents": parents, "author_email": author, "message": message.strip()}


def commit_diff(root: Path, metadata: dict, options: list[str], paths: list[str]) -> str:
    parents = metadata["parents"]
    if parents:
        return _git(root, "diff", *DIFF_OPTIONS, *options, parents[0], metadata["commit"], "--", *paths)
    return _git(
        root, "diff-tree", "--root", "--no-commit-id", "-r", *DIFF_OPTIONS,
        *options, metadata["commit"], "--", *paths,
    )


def changed_ranges(patch: str) -> dict:
    """Return changed line intervals from a zero-context unified diff."""
    ranges = {"before": [], "after": []}
    for match in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", patch, re.MULTILINE):
        for side, offset in (("before", 1), ("after", 3)):
            start = int(match.group(offset))
            count = int(match.group(offset + 1) or "1")
            if count:
                ranges[side].append((start, start + count - 1))
    return ranges


def verify_git_evidence(report: dict, catalog: dict, repo: str | Path) -> int:
    """Verify changed-line references and author attribution, not skill semantics."""
    validate_report(report, catalog)
    if report["schema_version"] == 2:
        from .repository import Pygit2Reader
        from .repository_analysis import verify_repository_evidence

        reader = Pygit2Reader(Path(repo))
        try:
            return verify_repository_evidence(report, catalog, reader)
        finally:
            reader.close()
    root = Path(_git(Path(repo), "rev-parse", "--show-toplevel").strip())
    metadata_by_commit = {}
    ranges_by_file = {}
    verified_files = set()
    checked = 0
    for assessment in report["assessments"]:
        for evidence in assessment["evidence"]:
            commit = evidence["commit"]
            if commit not in metadata_by_commit:
                metadata_by_commit[commit] = commit_metadata(root, commit, report["contributor_email"])
            metadata = metadata_by_commit[commit]
            parents = metadata["parents"]
            file_key = (commit, evidence["path"], evidence["side"])
            if file_key not in verified_files:
                if evidence["side"] == "before" and not parents:
                    raise ValidationError(f"{commit}: a root commit has no before side")
                revision = parents[0] if evidence["side"] == "before" else commit
                object_type = _git(root, "cat-file", "-t", f"{revision}:{evidence['path']}").strip()
                if object_type != "blob":
                    raise ValidationError(f"{commit}:{evidence['path']}: citation must reference a file")
                verified_files.add(file_key)
            key = (commit, evidence["path"])
            if key not in ranges_by_file:
                patch = commit_diff(root, metadata, ["-p", "--unified=0"], [evidence["path"]])
                ranges_by_file[key] = changed_ranges(patch)
            start, end = evidence["start_line"], evidence["end_line"]
            if not any(low <= start <= end <= high for low, high in ranges_by_file[key][evidence["side"]]):
                raise ValidationError(
                    f"{commit}:{evidence['path']}:{start}-{end} ({evidence['side']}): "
                    "citation must contain only changed lines"
                )
            checked += 1
    return checked
