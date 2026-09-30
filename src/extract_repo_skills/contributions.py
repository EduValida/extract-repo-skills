"""Collect one attributed commit and bounded context for semantic assessment."""

import json
from pathlib import Path

from .contracts import ValidationError
from .git_evidence import _git, changed_ranges, commit_diff, commit_metadata


def collect_commit(
    repo: str | Path,
    revision: str,
    contributor_email: str,
    *,
    max_chars: int = 60_000,
    max_files: int = 50,
) -> dict:
    """Collect committed content only; refuse oversized inputs without truncation."""
    if max_chars < 1 or max_files < 1:
        raise ValidationError("Collection limits must be positive")
    root = Path(_git(Path(repo), "rev-parse", "--show-toplevel").strip())
    commit = _git(root, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").strip()
    metadata = commit_metadata(root, commit, contributor_email)
    names = commit_diff(root, metadata, ["--name-only", "-z"], []).split("\x00")
    paths = [name for name in names if name]
    if len(paths) > max_files:
        raise ValidationError(f"Commit changes {len(paths)} files; limit is {max_files}")
    result = {"commit": commit, "message": metadata["message"], "files": []}
    for path in paths:
        patch = commit_diff(root, metadata, ["-p", "--unified=0"], [path])
        ranges = changed_ranges(patch)
        if not any(ranges.values()):
            raise ValidationError(
                f"{path}: no citable text changes (binary, mode-only, or empty file change); "
                "this commit needs manual review"
            )
        # Symlinks and submodules have diff lines but are not source file content.
        for revision_id in [*metadata["parents"], commit]:
            entry = _git(root, "ls-tree", "-z", revision_id, "--", path)
            if entry and entry.split(" ", 1)[0] not in ("100644", "100755"):
                raise ValidationError(f"{path}: non-regular file changes require manual review")
        context_patch = commit_diff(root, metadata, ["-p", "--unified=5"], [path])
        result["files"].append({"path": path, "changed_lines": ranges, "patch": context_patch})
        if len(json.dumps(result, ensure_ascii=False)) > max_chars:
            raise ValidationError(f"Commit context exceeds {max_chars} characters; nothing was sent to a model")
    if not result["files"]:
        raise ValidationError("Commit has no changes to assess")
    return result
