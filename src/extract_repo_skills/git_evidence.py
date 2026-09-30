"""Check citation provenance without executing repository code or diff drivers."""

import re
import subprocess
from pathlib import Path

from .contracts import ValidationError, validate_report


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


def verify_git_evidence(report: dict, catalog: dict, repo: str | Path) -> int:
    """Verify changed-line references and author attribution, not skill semantics."""
    validate_report(report, catalog)
    root = Path(_git(Path(repo), "rev-parse", "--show-toplevel").strip())
    parents_by_commit = {}
    ranges_by_file = {}
    verified_files = set()
    checked = 0
    for assessment in report["assessments"]:
        for evidence in assessment["evidence"]:
            commit = evidence["commit"]
            if commit not in parents_by_commit:
                parts = _git(root, "rev-list", "--parents", "-n", "1", commit, "--").split()
                if not parts or parts[0] != commit:
                    raise ValidationError(f"{commit}: evidence must identify a commit")
                parents = parts[1:]
                if len(parents) > 1:
                    raise ValidationError(f"{commit}: merge attribution requires review")
                metadata = _git(root, "show", "-s", "--format=%ae%n%B", commit, "--")
                author, _, message = metadata.partition("\n")
                if author != report["contributor_email"]:
                    raise ValidationError(f"{commit}: author does not match contributor_email")
                if re.search(r"^\s*co-authored-by\s*:", message, re.IGNORECASE | re.MULTILINE):
                    raise ValidationError(f"{commit}: coauthor attribution requires review")
                if not parents:
                    # Shallow boundaries must not be treated as genuine root commits.
                    raw = _git(root, "cat-file", "-p", commit).split("\n\n", 1)[0]
                    if any(line.startswith("parent ") for line in raw.splitlines()):
                        raise ValidationError(f"{commit}: parent history is missing; use a complete checkout")
                parents_by_commit[commit] = parents
            file_key = (commit, evidence["path"], evidence["side"])
            if file_key not in verified_files:
                parents = parents_by_commit[commit]
                if evidence["side"] == "before" and not parents:
                    raise ValidationError(f"{commit}: a root commit has no before side")
                revision = parents[0] if evidence["side"] == "before" else commit
                object_type = _git(root, "cat-file", "-t", f"{revision}:{evidence['path']}").strip()
                if object_type != "blob":
                    raise ValidationError(f"{commit}:{evidence['path']}: citation must reference a file")
                verified_files.add(file_key)
            key = (commit, evidence["path"])
            if key not in ranges_by_file:
                parents = parents_by_commit[commit]
                options = [
                    "--no-ext-diff", "--no-textconv", "--no-renames", "--unified=0",
                    "--inter-hunk-context=0", "--no-color", "--diff-algorithm=myers",
                    "--no-indent-heuristic", "--ignore-submodules=none",
                ]
                if parents:
                    patch = _git(root, "diff", *options, parents[0], commit, "--", evidence["path"])
                else:
                    patch = _git(root, "diff-tree", "--root", "--no-commit-id", "-p", *options, commit, "--", evidence["path"])
                ranges = {"before": [], "after": []}
                for match in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", patch, re.MULTILINE):
                    for side, offset in (("before", 1), ("after", 3)):
                        start = int(match.group(offset))
                        count = int(match.group(offset + 1) or "1")
                        if count:
                            ranges[side].append((start, start + count - 1))
                ranges_by_file[key] = ranges
            start, end = evidence["start_line"], evidence["end_line"]
            if not any(low <= start <= end <= high for low, high in ranges_by_file[key][evidence["side"]]):
                raise ValidationError(
                    f"{commit}:{evidence['path']}:{start}-{end} ({evidence['side']}): "
                    "citation must contain only changed lines"
                )
            checked += 1
    return checked
