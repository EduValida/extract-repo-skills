"""Application service for repository/user assessments, independent of HTTP."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .analysis import INSTRUCTIONS, _object_schema
from .artifacts import (
    CollectionLimits,
    canonical,
    collect_bundle,
    collect_change,
    digest,
)
from .contracts import (
    ValidationError,
    catalog_identity,
    read_json,
    validate_catalog,
    validate_report,
)
from .repository import AnalysisError, open_repository

DEFAULT_CATALOG = Path(__file__).parent / "data" / "roadmap-starter.json"
PROMPT_VERSION = "repository-artifacts-v2"


@dataclass(frozen=True)
class RepositoryAnalysisRequest:
    repository: str
    emails: list[str]
    ref: str = "HEAD"
    max_commits: int = 50
    dry_run: bool = False

    def __post_init__(self):
        if (
            not isinstance(self.repository, str)
            or not 1 <= len(self.repository) <= 2048
        ):
            raise AnalysisError(
                "invalid_request", "Repository must be a nonempty source identifier"
            )
        if (
            not isinstance(self.emails, list)
            or not 1 <= len(self.emails) <= 20
            or any(
                not isinstance(email, str)
                or not re.fullmatch(r"[^\s@]{1,200}@[^\s@]{1,200}", email)
                for email in self.emails
            )
            or len(set(self.emails)) != len(self.emails)
        ):
            raise AnalysisError(
                "invalid_request", "Supply 1–20 unique exact author emails"
            )
        if (
            not isinstance(self.ref, str)
            or not 1 <= len(self.ref) <= 256
            or self.ref.startswith("-")
            or any(ord(char) < 32 for char in self.ref)
        ):
            raise AnalysisError("invalid_request", "Invalid Git reference")
        if type(self.max_commits) is not int or not 1 <= self.max_commits <= 100:
            raise AnalysisError(
                "invalid_request", "max_commits must be between 1 and 100"
            )
        if type(self.dry_run) is not bool:
            raise AnalysisError("invalid_request", "dry_run must be boolean")

    @classmethod
    def from_dict(cls, value: dict):
        if (
            not isinstance(value, dict)
            or set(value) - {"repository", "user", "ref", "max_commits", "dry_run"}
            or not {"repository", "user"} <= set(value)
            or not isinstance(value["user"], dict)
            or set(value["user"]) != {"emails"}
        ):
            raise AnalysisError(
                "invalid_request",
                "Expected repository, user.emails and optional analysis controls",
            )
        return cls(
            value["repository"],
            value["user"]["emails"],
            value.get("ref", "HEAD"),
            value.get("max_commits", 50),
            value.get("dry_run", False),
        )


@dataclass(frozen=True)
class ServiceSettings:
    local_repositories: dict[str, str] = field(default_factory=dict)
    allowed_remote_hosts: tuple[str, ...] = ()
    catalog_path: str = str(DEFAULT_CATALOG)
    model: str = "deepseek-flash"
    max_output_tokens: int = 8_000
    timeout_seconds: int = 120
    max_concurrency: int = 2
    cloc_executable: str = "cloc"
    limits: CollectionLimits = field(default_factory=CollectionLimits)

    def __post_init__(self):
        if (
            not isinstance(self.cloc_executable, str)
            or not self.cloc_executable.strip()
        ):
            raise ValueError(
                "Configure a nonempty cloc executable path or command name"
            )
        if not isinstance(self.local_repositories, dict) or any(
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", key)
            or not isinstance(path, str)
            or not Path(path).is_absolute()
            for key, path in self.local_repositories.items()
        ):
            raise ValueError(
                "Local repositories must map names to absolute server paths"
            )
        for value in (
            self.timeout_seconds,
            self.max_concurrency,
            self.max_output_tokens,
            *vars(self.limits).values(),
        ):
            if type(value) is not int or value < 1:
                raise ValueError("Service limits must be positive integers")

    @classmethod
    def from_env(cls):
        from .contracts import parse_json

        return cls(
            local_repositories=parse_json(
                os.getenv("EXTRACT_LOCAL_REPOSITORIES", "{}")
            ),
            allowed_remote_hosts=tuple(
                host.strip().lower()
                for host in os.getenv("EXTRACT_REMOTE_HOSTS", "").split(",")
                if host.strip()
            ),
            catalog_path=os.getenv("EXTRACT_CATALOG", str(DEFAULT_CATALOG)),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            max_output_tokens=int(os.getenv("EXTRACT_MAX_OUTPUT_TOKENS", "8000")),
            timeout_seconds=int(os.getenv("EXTRACT_TIMEOUT_SECONDS", "120")),
            max_concurrency=int(os.getenv("EXTRACT_MAX_CONCURRENCY", "2")),
            cloc_executable=os.getenv("EXTRACT_CLOC_EXECUTABLE", "cloc"),
            limits=CollectionLimits(
                max_input_chars=int(os.getenv("EXTRACT_MAX_INPUT_CHARS", "60000"))
            ),
        )


def prepare_repository_analysis(
    bundle: dict, catalog: dict, max_input_chars: int
) -> dict:
    manifest = bundle["manifest"]
    patches = bundle["artifacts"]["git-diff.json"]["commits"]
    evidence = _object_schema(
        {
            "commit": {"type": "string", "enum": [item["commit"] for item in patches]},
            "path": {
                "type": "string",
                "enum": sorted(
                    {file["path"] for item in patches for file in item["files"]}
                ),
            },
            "side": {"type": "string", "enum": ["before", "after"]},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        }
    )
    assessment = _object_schema(
        {
            "decision": {
                "type": "string",
                "enum": ["supported", "insufficient_evidence", "catalog_gap"],
            },
            "skill_id": {
                "type": ["string", "null"],
                "enum": [*[item["id"] for item in catalog["skills"]], None],
            },
            "rationale": {"type": "string"},
            "evidence": {"type": "array", "items": evidence, "minItems": 1},
        }
    )
    schema = _object_schema({"assessments": {"type": "array", "items": assessment}})
    instructions = (
        INSTRUCTIONS
        + """
The input contains five static artifacts for multiple commits by one contributor.
Only git-diff.json changed lines can support citations. git-log.json establishes
recorded authorship and chronology, not skill substance. languages.json,
project-context.json and contribution-context.json supply context only; do not
attribute their unchanged contents to the contributor. The contributor may have
multiple explicitly selected author emails. Assess only the selected commits.
Report limited coverage and abstain when context does not support a conclusion.
cloc line counts use a different denominator from byte-based language percentages.
Neither code volume nor Lizard complexity, function size, tokens or parameters
establish a skill or proficiency. Function comparisons are heuristic context;
unmatched/ambiguous functions are not proven additions, removals or refactorings.
Tool coverage gaps and no functions detected do not establish missing skills.
"""
        + "\nReturn JSON matching this schema:\n"
        + canonical(schema)
    )
    context = {
        "schema_version": 2,
        "catalog": catalog_identity(catalog),
        "repository": manifest["repository"],
        "user": manifest["user"],
        "reference": manifest["reference"],
        "selection": manifest["selection"],
    }
    request = {
        "messages": [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": canonical({"catalog": catalog, "bundle": bundle}),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    if len(canonical(request)) > max_input_chars:
        raise AnalysisError(
            "input_limit",
            "Complete model input exceeds the limit; nothing was sent",
            413,
        )
    return {
        "prompt_version": PROMPT_VERSION,
        "input_sha256": digest(request),
        "bundle_sha256": digest(bundle),
        "commits": [item["commit"] for item in patches],
        "report_context": context,
        "request": request,
    }


def verify_repository_evidence(report: dict, catalog: dict, reader) -> int:
    validate_report(report, catalog)
    selection = report["selection"]["selected"]
    recorded = {item["commit"]: item["author_email"] for item in selection}
    # Verify selection and attribution even for an empty assessments array.
    history = reader.history(report["reference"]["resolved"], 10_000)
    reachable = {item["commit"] for item in history}
    matching = sorted(
        (
            item
            for item in history
            if item["author"]["email"] in report["user"]["emails"]
        ),
        key=lambda item: (-item["author"]["timestamp"], item["commit"]),
    )
    scope = report["selection"]
    expected = {item["commit"] for item in matching[: scope["max_commits"]]}
    declared = set(recorded) | {item["commit"] for item in scope["excluded"]}
    if (
        scope["reachable_commits"] != len(history)
        or scope["matching_commits"] != len(matching)
        or expected != declared
    ):
        raise ValidationError(
            "Reported selection or coverage does not match the Git history"
        )
    cache = {}
    for commit, email in recorded.items():
        if commit not in reachable:
            raise ValidationError("Selected commit is outside the resolved reference")
        metadata = reader.metadata(commit)
        if (
            metadata["author"]["email"] != email
            or email not in report["user"]["emails"]
        ):
            raise ValidationError(
                "Selected commit author does not match the requested identities"
            )
        if len(metadata["parents"]) > 1 or re.search(
            r"^\s*co-authored-by\s*:", metadata["message"], re.IGNORECASE | re.MULTILINE
        ):
            raise ValidationError("Selected commit attribution requires review")
        cache[commit] = metadata
    patches = {}
    checked = 0
    for assessment in report["assessments"]:
        for evidence in assessment["evidence"]:
            commit, path, side = evidence["commit"], evidence["path"], evidence["side"]
            if commit not in recorded:
                raise ValidationError("Citation is outside the analyzed selection")
            if commit not in patches:
                files, _ = collect_change(reader, cache[commit], CollectionLimits())
                patches[commit] = {
                    item["path"]: item["changed_lines"] for item in files
                }
            ranges = patches[commit].get(path, {}).get(side, [])
            if not any(
                start <= evidence["start_line"] <= evidence["end_line"] <= end
                for start, end in ranges
            ):
                raise ValidationError(
                    "Citation must contain only selected changed lines"
                )
            checked += 1
    return checked


def validate_repository_assessment(
    output: dict, prepared: dict, catalog: dict, reader
) -> dict:
    if not isinstance(output, dict) or set(output) != {"assessments"}:
        raise ValidationError("Model output must contain exactly an assessments array")
    report = {**prepared["report_context"], "assessments": output["assessments"]}
    verify_repository_evidence(report, catalog, reader)
    return report


def analyze_repository(
    request: RepositoryAnalysisRequest,
    settings: ServiceSettings,
    workspace: Path,
    *,
    provider=None,
) -> dict:
    catalog = validate_catalog(read_json(settings.catalog_path))
    with open_repository(
        request.repository,
        settings.local_repositories,
        settings.allowed_remote_hosts,
        workspace,
    ) as reader:
        bundle = collect_bundle(
            reader,
            request.repository,
            request.emails,
            request.ref,
            request.max_commits,
            settings.limits,
            cloc_executable=settings.cloc_executable,
            workspace=workspace,
        )
        prepared = prepare_repository_analysis(
            bundle, catalog, settings.limits.max_input_chars
        )
        if request.dry_run:
            return {"status": "preview", "bundle": bundle, "prepared": prepared}
        if not prepared["commits"]:
            report = {**prepared["report_context"], "assessments": []}
            validate_report(report, catalog)
            return {
                "status": "no_eligible_contributions",
                "report": report,
                "run": {
                    "provider": None,
                    "reason": "No eligible commits in the requested scope",
                    "human_review_required": True,
                    "git_evidence_verified": True,
                    "bundle_sha256": prepared["bundle_sha256"],
                    "report_sha256": digest(report),
                    "artifact_sha256": bundle["manifest"]["artifact_sha256"],
                    "prompt_version": PROMPT_VERSION,
                    "input_sha256": prepared["input_sha256"],
                },
            }
        if provider is None:
            from .deepseek_provider import assess

            provider = assess
        try:
            output, run = provider(
                prepared, settings.model, max_output_tokens=settings.max_output_tokens
            )
            report = validate_repository_assessment(output, prepared, catalog, reader)
        except ValidationError as exc:
            raise AnalysisError(
                "assessment_rejected",
                "Provider assessment failed or did not pass evidence validation",
                502,
            ) from exc
        return {
            "status": "completed",
            "report": report,
            "run": {
                **run,
                "git_evidence_verified": True,
                "human_review_required": True,
                "bundle_sha256": prepared["bundle_sha256"],
                "artifact_sha256": bundle["manifest"]["artifact_sha256"],
                "assessment_sha256": digest(output),
                "report_sha256": digest(report),
                "prompt_version": PROMPT_VERSION,
                "input_sha256": prepared["input_sha256"],
            },
        }
