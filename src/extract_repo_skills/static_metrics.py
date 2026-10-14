"""Static counters on committed text; metrics are context, never skill evidence."""

import io
import os
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stderr
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

import lizard

from .contracts import ValidationError, parse_json
from .repository import AnalysisError

CLOC_OPTIONS = ("--json", "--by-file", "--skip-uniqueness", "--quiet", "--timeout=0")
FUNCTION_FIELDS = ("nloc", "cyclomatic_complexity", "token_count", "parameter_count")
LIZARD_POLICY = "changed-functions-v1"


def _cloc_command(executable: str) -> str:
    found = shutil.which(executable)
    if found is None and executable == "cloc":
        candidate = Path(sys.executable).parent / "cloc"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            found = str(candidate)
    if found is None:
        raise AnalysisError(
            "cloc_unavailable", "Install cloc or configure EXTRACT_CLOC_EXECUTABLE", 503
        )
    return str(Path(found).resolve())


def _run_cloc(command: str, root: Path, *args: str) -> str:
    scratch = root / "scratch"
    scratch.mkdir(exist_ok=True)
    config = root / "options.txt"
    config.touch()
    try:
        result = subprocess.run(
            [command, f"--config={config}", *args],
            cwd=root,
            env={
                **os.environ,
                "TMPDIR": str(scratch),
                "TMP": str(scratch),
                "TEMP": str(scratch),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError(
            "cloc_timeout", "cloc exceeded its 30-second time limit", 504
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise AnalysisError(
            "cloc_failed", "Cannot read a valid cloc result", 502
        ) from exc
    if result.returncode or result.stderr.strip() or len(result.stdout) > 5_000_000:
        raise AnalysisError(
            "cloc_failed", "cloc did not return a complete bounded result", 502
        )
    return result.stdout


def _line_summary(files: list[dict]) -> dict:
    by_language = {}
    for item in files:
        aggregate = by_language.setdefault(
            item["language"], {"files": 0, "code": 0, "comment": 0, "blank": 0}
        )
        aggregate["files"] += 1
        for field in ("code", "comment", "blank"):
            aggregate[field] += item[field]
    total = sum(item["code"] for item in files)
    return {
        "total_code_lines": total,
        "languages": [
            {
                "name": name,
                **metrics,
                "percentage_of_code_lines": round(metrics["code"] * 100 / total, 6)
                if total
                else 0,
            }
            for name, metrics in sorted(by_language.items())
        ],
    }


def cloc_snapshot(
    inputs: dict[str, tuple[str, str]],
    touched: set[str],
    *,
    executable: str = "cloc",
    workspace: Path | None = None,
) -> dict:
    """Count only preselected regular UTF-8 blobs, preserving basename detection."""
    command = _cloc_command(executable)
    with TemporaryDirectory(prefix="cloc-", dir=workspace) as directory:
        root = Path(directory)
        tool_version = _run_cloc(command, root, "--version").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)+", tool_version):
            raise AnalysisError("cloc_failed", "cloc returned an invalid version", 502)
        source = root / "inputs"
        source.mkdir()
        mapping = {}
        for index, (path, (_blob, text)) in enumerate(sorted(inputs.items())):
            # Independent numeric directories prevent traversal and retain names
            # such as Dockerfile. No Git checkout, source scripts, or configs run.
            parent = source / str(index)
            parent.mkdir()
            basename = re.sub(r"[^A-Za-z0-9_.+-]", "_", PurePosixPath(path).name)
            if basename in {"", ".", ".."}:
                basename = "source"
            filename = parent / basename
            filename.write_bytes(text.encode("utf-8"))
            mapping[filename.relative_to(root).as_posix()] = path
        raw = _run_cloc(command, root, *CLOC_OPTIONS, "inputs") if inputs else ""
        try:
            report = parse_json(raw) if raw.strip() else {}
            if not isinstance(report, dict):
                raise ValidationError("Expected a cloc object")
            files = []
            for filename, counts in report.items():
                if filename in {"header", "SUM"}:
                    continue
                if filename not in mapping or not isinstance(counts, dict):
                    raise ValidationError("Unexpected cloc file")
                if (
                    not isinstance(counts.get("language"), str)
                    or not counts["language"]
                ):
                    raise ValidationError("Invalid cloc language")
                if any(
                    type(counts.get(field)) is not int or counts[field] < 0
                    for field in ("code", "comment", "blank")
                ):
                    raise ValidationError("Invalid cloc counters")
                path = mapping[filename]
                files.append(
                    {
                        "path": path,
                        "blob": inputs[path][0],
                        "language": counts["language"],
                        **{
                            field: counts[field]
                            for field in ("code", "comment", "blank")
                        },
                    }
                )
            files.sort(key=lambda item: item["path"])
            if files and (
                report.get("header", {}).get("cloc_version") != tool_version
                or any(
                    report.get("SUM", {}).get(field)
                    != sum(item[field] for item in files)
                    for field in ("code", "comment", "blank")
                )
            ):
                raise ValidationError("Inconsistent cloc totals or version")
        except (ValidationError, AttributeError, TypeError) as exc:
            raise AnalysisError(
                "cloc_failed", "cloc returned invalid or inconsistent counters", 502
            ) from exc
    counted = {item["path"] for item in files}
    return {
        "tool": "cloc",
        "version": tool_version,
        "role": "context_only",
        "status": "ok" if files else "no_applicable_files",
        "configuration": {
            "options": list(CLOC_OPTIONS),
            "user_configuration": False,
            "inputs": "selected regular UTF-8 snapshot blobs after path exclusions",
            "filenames": "non-ASCII and special characters replaced by underscores; safe basename/suffix preserved",
        },
        "denominator": "code lines excluding blank lines and comments; independent of byte percentages",
        "project": _line_summary(files),
        "contributor_touched_snapshot_files": _line_summary(
            [item for item in files if item["path"] in touched]
        ),
        "files": files,
        "not_counted": [
            {"path": path, "reason": "not_counted_by_cloc"}
            for path in sorted(set(inputs) - counted)
        ],
    }


def _comment_lines(tokens, reader):
    """Discard comments while keeping LF positions, ignoring suppression directives."""
    for token in tokens:
        if reader.get_comment_from_token(token) is None:
            yield token
        else:
            for _ in range(token.count("\n")):
                yield "\n"


def _functions(path: str, text: str) -> list[dict]:
    analyzer = lizard.FileAnalyzer(
        [
            lizard.preprocessing,
            _comment_lines,
            lizard.line_counter,
            lizard.token_counter,
            lizard.condition_counter,
        ]
    )
    diagnostics = io.StringIO()
    try:
        with redirect_stderr(diagnostics):
            result = analyzer.analyze_source_code(path, text)
    except (ValueError, IndexError, RecursionError) as exc:
        raise AnalysisError(
            "lizard_failed", "Lizard could not analyze the complete file", 502
        ) from exc
    if diagnostics.getvalue():
        raise AnalysisError(
            "lizard_failed", "Lizard did not complete file analysis", 502
        )
    return [
        {
            "name": item.name,
            "signature": item.long_name,
            "start_line": item.start_line,
            "end_line": item.end_line,
            **{field: getattr(item, field) for field in FUNCTION_FIELDS},
        }
        for item in result.function_list
    ]


def _intersections(function: dict, ranges: list[list[int]]) -> list[list[int]]:
    return [
        [max(start, function["start_line"]), min(end, function["end_line"])]
        for start, end in ranges
        if start <= function["end_line"] and end >= function["start_line"]
    ]


def lizard_change(
    path: str, before: dict | None, after: dict | None, changed_lines: dict
) -> dict:
    """Retain changed functions and baseline counterparts; pair conservatively."""
    supported = lizard.get_reader_for(path) is not None
    parsed, sides = {}, {}
    affected = set()
    for side, source in (("before", before), ("after", after)):
        functions = _functions(path, source["text"]) if source and supported else []
        for function in functions:
            function["changed_lines"] = _intersections(function, changed_lines[side])
            if function["changed_lines"]:
                affected.add(function["name"])
        parsed[side] = functions
        sides[side] = {
            "snapshot": source["snapshot"] if source else None,
            "blob": source["blob"] if source else None,
            "status": "file_absent"
            if not source
            else "not_applicable"
            if not supported
            else "ok"
            if functions
            else "no_functions_detected",
        }
    comparisons = []
    for name in sorted(affected):
        left = [item for item in parsed["before"] if item["name"] == name]
        right = [item for item in parsed["after"] if item["name"] == name]
        paired = len(left) == len(right) == 1
        comparisons.append(
            {
                "name": name,
                "status": "paired_by_unique_name"
                if paired
                else "ambiguous"
                if max(len(left), len(right)) > 1
                else "unmatched_after"
                if right
                else "unmatched_before",
                "delta": {
                    field: right[0][field] - left[0][field] for field in FUNCTION_FIELDS
                }
                if paired
                else None,
            }
        )
    for side, result in sides.items():
        result["functions"] = [
            item for item in parsed[side] if item["name"] in affected
        ]
    return {
        "path": path,
        "role": "context_only",
        "status": "not_applicable"
        if not supported
        else "ok"
        if affected
        else "no_changed_functions_detected",
        "before": sides["before"],
        "after": sides["after"],
        "comparisons": comparisons,
    }


def lizard_metadata() -> dict:
    return {
        "tool": "lizard",
        "version": version("lizard"),
        "policy_version": LIZARD_POLICY,
        "role": "context_only",
        "configuration": {
            "repository_suppressions": False,
            "generated_code_directives": False,
            "matching": "unique function name within the same path; no rename or cross-file matching",
            "scope": "functions intersecting changed lines and their same-name baseline counterparts",
        },
    }
