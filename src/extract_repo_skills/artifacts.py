"""Five static artifacts with explicit scope, byte accounting and provenance."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

import pygit2
import pygments
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound

from .repository import AnalysisError, GitRepositoryReader
from .static_metrics import cloc_snapshot, lizard_change, lizard_metadata

ARTIFACT_NAMES = (
    "git-log.json",
    "git-diff.json",
    "languages.json",
    "project-context.json",
    "contribution-context.json",
)
POLICY_VERSION = "static-artifacts-v2"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CollectionLimits:
    max_history: int = 10_000
    max_tree_files: int = 5_000
    max_changed_files: int = 50
    max_blob_bytes: int = 1_000_000
    max_snapshot_bytes: int = 20_000_000
    max_input_chars: int = 60_000
    max_context_chars: int = 8_000
    context_lines: int = 5


def _regular(entry) -> bool:
    return entry.mode in (0o100644, 0o100755)


def _text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _ranges(lines: list[int]) -> list[list[int]]:
    result = []
    for number in sorted(set(lines)):
        if result and number == result[-1][1] + 1:
            result[-1][1] = number
        else:
            result.append([number, number])
    return result


def collect_change(
    reader: GitRepositoryReader,
    metadata: dict,
    limits: CollectionLimits,
    *,
    function_metrics: list | None = None,
) -> tuple[list, list]:
    commit, parents = metadata["commit"], metadata["parents"]
    paths = reader.changed_paths(commit, parents)
    if len(paths) > limits.max_changed_files:
        raise AnalysisError(
            "changed_files_limit", "Commit exceeds the changed-file limit", 413
        )
    if not paths:
        raise AnalysisError("empty_commit", "Commit has no changes to assess")
    snapshots = {
        "after": {
            entry.path: entry for entry in reader.entries(commit, limits.max_tree_files)
        }
    }
    snapshots["before"] = (
        {
            entry.path: entry
            for entry in reader.entries(parents[0], limits.max_tree_files)
        }
        if parents
        else {}
    )
    contents = {}
    for path in paths:
        if (
            PurePosixPath(path).as_posix() != path
            or ".." in PurePosixPath(path).parts
            or PurePosixPath(path).is_absolute()
        ):
            raise AnalysisError("unsupported_path", "Changed path is not normalized")
        for side, entries in snapshots.items():
            entry = entries.get(path)
            if entry:
                if not _regular(entry):
                    raise AnalysisError(
                        "non_regular_file", "Commit changes a symlink or submodule"
                    )
                value = _text(reader.blob(entry, limits.max_blob_bytes))
                if value is None:
                    raise AnalysisError(
                        "non_text_file", "Commit changes non-UTF-8 or binary content"
                    )
                contents[path, side] = value

    # Parse individual lines structurally, while keeping the existing C Git diff
    # algorithm, no-renames policy and context settings.
    diff = pygit2.Diff.parse_diff(reader.diff(commit, parents, limits.context_lines))
    changes, context = [], []
    for patch in diff:
        path = patch.delta.new_file.path or patch.delta.old_file.path
        changed = {"before": [], "after": []}
        for hunk in patch.hunks:
            for line in hunk.lines:
                if line.origin == "+":
                    changed["after"].append(line.new_lineno)
                elif line.origin == "-":
                    changed["before"].append(line.old_lineno)
        intervals = {side: _ranges(numbers) for side, numbers in changed.items()}
        if not any(intervals.values()):
            raise AnalysisError(
                "no_citable_changes",
                "Commit includes a change with no citable text lines",
            )
        changes.append({"path": path, "changed_lines": intervals, "patch": patch.text})
        if function_metrics is not None:
            sources = {}
            for side in ("before", "after"):
                entry = snapshots[side].get(path)
                sources[side] = (
                    {
                        "text": contents[path, side],
                        "blob": entry.oid,
                        "snapshot": parents[0] if side == "before" else commit,
                    }
                    if entry
                    else None
                )
            function_metrics.append(
                {
                    "commit": commit,
                    **lizard_change(
                        path,
                        sources["before"],
                        sources["after"],
                        intervals,
                    ),
                }
            )
        for side, ranges in intervals.items():
            if not ranges:
                continue
            text = contents[path, side]
            # Git line numbers are based on LF, not Python's additional Unicode
            # line separators. Retain exact content, including a missing final LF.
            lines = text.split("\n")
            lines = [line + "\n" for line in lines[:-1]] + (
                [lines[-1]] if lines[-1] else []
            )
            windows = []
            for start, end in ranges:
                low, high = (
                    max(1, start - limits.context_lines),
                    min(len(lines), end + limits.context_lines),
                )
                if windows and low <= windows[-1][1] + 1:
                    windows[-1][1] = max(windows[-1][1], high)
                else:
                    windows.append([low, high])
            snippets = [
                {
                    "start_line": start,
                    "end_line": end,
                    "text": "".join(lines[start - 1 : end]),
                }
                for start, end in windows
            ]
            if sum(len(item["text"]) for item in snippets) > limits.max_context_chars:
                raise AnalysisError(
                    "context_limit",
                    "Required contribution context exceeds the limit",
                    413,
                )
            entry = snapshots[side][path]
            context.append(
                {
                    "commit": commit,
                    "path": path,
                    "side": side,
                    "snapshot": parents[0] if side == "before" else commit,
                    "blob": entry.oid,
                    "snippets": snippets,
                    "role": "context_only",
                }
            )
    return changes, context


EXCLUDED_DIRS = {"vendor", "node_modules", "dist", "build", "target", "generated"}
EXCLUDED_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "Cargo.lock",
}
MANIFEST_NAMES = {
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "Makefile",
    "CMakeLists.txt",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yaml",
    "tox.ini",
    "pytest.ini",
    "tsconfig.json",
    ".gitlab-ci.yml",
    "Jenkinsfile",
    "azure-pipelines.yml",
    ".gitattributes",
}


def _language_exclusion(path: str) -> str | None:
    if set(PurePosixPath(path).parts[:-1]) & EXCLUDED_DIRS:
        return "directory_policy"
    if PurePosixPath(path).name in EXCLUDED_NAMES or path.endswith(
        (".min.js", ".min.css")
    ):
        return "filename_policy"
    return None


def _breakdown(counts: Counter) -> dict:
    total = sum(counts.values())
    return {
        "total_classified_bytes": total,
        "languages": [
            {
                "name": name,
                "bytes": size,
                "percentage": round(size * 100 / total, 6) if total else 0,
            }
            for name, size in sorted(counts.items())
        ],
    }


def snapshot_context(
    reader: GitRepositoryReader,
    commit: str,
    touched: set[str],
    limits: CollectionLimits,
    *,
    cloc_executable: str = "cloc",
    workspace: Path | None = None,
) -> tuple[dict, dict]:
    entries = reader.entries(commit, limits.max_tree_files)
    if (
        sum(entry.size for entry in entries if _regular(entry))
        > limits.max_snapshot_bytes
    ):
        raise AnalysisError("snapshot_limit", "Snapshot exceeds the byte limit", 413)
    counts, modified_counts, excluded, manifests = Counter(), Counter(), [], []
    cloc_inputs = {}
    for entry in entries:
        reason = (
            "non_regular_file"
            if not _regular(entry)
            else _language_exclusion(entry.path)
        )
        text = None
        if not reason:
            text = _text(reader.blob(entry, limits.max_blob_bytes))
            if text is None:
                reason = "binary_or_non_utf8"
        if text is not None:
            cloc_inputs[entry.path] = (entry.oid, text)
        if not reason:
            try:
                name = get_lexer_for_filename(entry.path, code=text).name
            except ClassNotFound:
                reason = "unclassified"
            else:
                counts[name] += entry.size
                if entry.path in touched:
                    modified_counts[name] += entry.size
        if reason:
            excluded.append({"path": entry.path, "reason": reason, "bytes": entry.size})
        is_manifest = PurePosixPath(
            entry.path
        ).name in MANIFEST_NAMES or entry.path.startswith(
            (".github/workflows/", ".circleci/")
        )
        if is_manifest and _regular(entry):
            if text is None:
                text = _text(reader.blob(entry, limits.max_blob_bytes))
            if text is not None:
                manifests.append(
                    {
                        "path": entry.path,
                        "snapshot": commit,
                        "blob": entry.oid,
                        "text": text,
                        "role": "context_only",
                    }
                )
    language = {
        "snapshot": commit,
        "role": "context_only",
        "classifier": "pygments",
        "classifier_version": pygments.__version__,
        "policy_version": POLICY_VERSION,
        "denominator": "classified regular UTF-8 text bytes after explicit exclusions",
        "project": _breakdown(counts),
        "contributor_touched_snapshot_files": _breakdown(modified_counts),
        "contributor_summary_note": "Whole files at the reference SHA, not bytes authored by the contributor; deleted files are absent",
        "excluded": excluded,
        "policy": {
            "directories": sorted(EXCLUDED_DIRS),
            "filenames": sorted(EXCLUDED_NAMES),
            "minified_suffixes": [".min.js", ".min.css"],
            "gitattributes_overrides": False,
        },
    }
    project = {
        "snapshot": commit,
        "role": "context_only",
        "tree": [asdict(entry) for entry in entries],
        "manifests": manifests,
    }
    language["line_counts"] = cloc_snapshot(
        cloc_inputs,
        touched,
        executable=cloc_executable,
        workspace=workspace,
    )
    return language, project


def collect_bundle(
    reader: GitRepositoryReader,
    repository: str,
    emails: list[str],
    ref: str,
    max_commits: int,
    limits: CollectionLimits,
    *,
    cloc_executable: str = "cloc",
    workspace: Path | None = None,
) -> dict:
    resolved = reader.resolve(ref)
    history = reader.history(resolved, limits.max_history)
    matching = sorted(
        (item for item in history if item["author"]["email"] in emails),
        key=lambda item: (-item["author"]["timestamp"], item["commit"]),
    )
    selected, excluded, patches, contexts = [], [], [], []
    functions = []
    for item in matching[:max_commits]:
        reason = None
        if len(item["parents"]) > 1:
            reason = "merge_requires_review"
        elif re.search(
            r"^\s*co-authored-by\s*:", item["message"], re.IGNORECASE | re.MULTILINE
        ):
            reason = "coauthor_requires_review"
        if reason:
            excluded.append({"commit": item["commit"], "reason": reason})
            continue
        try:
            commit_functions = []
            files, snippets = collect_change(
                reader, item, limits, function_metrics=commit_functions
            )
        except AnalysisError as exc:
            if exc.status != 422 or exc.code not in {
                "empty_commit",
                "non_regular_file",
                "non_text_file",
                "no_citable_changes",
                "unsupported_path",
                "unsupported_encoding",
            }:
                raise
            excluded.append({"commit": item["commit"], "reason": exc.code})
            continue
        selected.append(item)
        patches.append({"commit": item["commit"], "files": files})
        contexts.extend(snippets)
        functions.extend(commit_functions)
    touched = {file["path"] for item in patches for file in item["files"]}
    languages, project = snapshot_context(
        reader,
        resolved,
        touched,
        limits,
        cloc_executable=cloc_executable,
        workspace=workspace,
    )
    artifacts = dict(
        zip(
            ARTIFACT_NAMES,
            [
                {"commits": selected},
                {"commits": patches},
                languages,
                project,
                {
                    "role": "context_only",
                    "entries": contexts,
                    "function_metrics": {**lizard_metadata(), "files": functions},
                },
            ],
            strict=True,
        )
    )
    manifest = {
        "schema_version": 1,
        "repository": repository,
        "user": {
            "emails": sorted(emails),
            "attribution": "exact_recorded_author_email",
        },
        "reference": {"requested": ref, "resolved": resolved},
        "policy_version": POLICY_VERSION,
        "reader": reader.version,
        "limits": asdict(limits),
        "selection": {
            "max_commits": max_commits,
            "order": "author_timestamp_desc_then_sha_asc",
            "reachable_commits": len(history),
            "matching_commits": len(matching),
            "considered_commits": min(len(matching), max_commits),
            "omitted_by_commit_limit": max(0, len(matching) - max_commits),
            "selected": [
                {"commit": item["commit"], "author_email": item["author"]["email"]}
                for item in selected
            ],
            "excluded": excluded,
            "coverage_complete": len(matching) <= max_commits and not excluded,
        },
        "artifact_sha256": {name: digest(value) for name, value in artifacts.items()},
    }
    bundle = {"manifest": manifest, "artifacts": artifacts}
    if len(canonical(bundle)) > limits.max_input_chars:
        raise AnalysisError(
            "input_limit",
            "Artifact bundle exceeds the input limit; nothing was sent",
            413,
        )
    return bundle
