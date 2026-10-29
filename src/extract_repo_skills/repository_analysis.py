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
    skill: str
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
            not isinstance(self.skill, str)
            or not 1 <= len(self.skill) <= 100
            or not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", self.skill.strip().lower())
        ):
            raise AnalysisError(
                "invalid_request", "Skill must be a nonempty identifier (e.g. 'python', 'css')"
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
            or set(value) - {"repository", "user", "skill", "ref", "max_commits", "dry_run"}
            or not {"repository", "user", "skill"} <= set(value)
            or not isinstance(value["user"], dict)
            or set(value["user"]) != {"emails"}
        ):
            raise AnalysisError(
                "invalid_request",
                "Expected repository, user.emails, skill and optional analysis controls",
            )
        return cls(
            value["repository"],
            value["user"]["emails"],
            value["skill"],
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
        from dotenv import load_dotenv

        from .contracts import parse_json

        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)

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
                max_input_chars=int(os.getenv("EXTRACT_MAX_INPUT_CHARS", "100000")),
                max_context_chars=int(os.getenv("EXTRACT_MAX_CONTEXT_CHARS", "25000")),
            ),
        )


def resolve_skill_catalog(
    skill: str, settings: ServiceSettings
) -> tuple[dict, list[str], dict | None]:
    slug = skill.strip().lower()

    # 1. Se a skill estiver presente no catálogo configurado
    base_catalog = validate_catalog(read_json(settings.catalog_path))
    matched_ids = [
        s["id"]
        for s in base_catalog["skills"]
        if s["id"].startswith(f"{slug}.")
        or s["id"] == slug
        or slug in [a.lower() for a in s.get("aliases", [])]
    ]
    if matched_ids:
        # Se também houver um grafo compilado da skill, carrega a árvore
        graphs_dir = Path("catalogs/graphs")
        graph_file = graphs_dir / f"{slug}.json"
        tree = None
        if graph_file.exists():
            data = read_json(graph_file)
            raw_tree = data.get("tree", {})
            tree = {
                "title": raw_tree.get("title", slug),
                "topics": [
                    {
                        "name": t["name"],
                        "subtopics": [s["name"] for s in t.get("subtopics", [])],
                    }
                    for t in raw_tree.get("topics", [])
                ],
            }
        return base_catalog, matched_ids, tree

    # 2. Se a skill existir apenas como grafo compilado em catalogs/graphs/
    graphs_dir = Path("catalogs/graphs")
    graph_file = graphs_dir / f"{slug}.json"
    if graph_file.exists():
        data = read_json(graph_file)
        tree = data.get("tree", {})
        skills_list = []
        for topic in tree.get("topics", []):
            for sub in topic.get("subtopics", []):
                clean_name = re.sub(r"[^a-z0-9]+", "-", sub["name"].lower()).strip("-")
                sub_id = f"{slug}.{clean_name}" if clean_name else f"{slug}.topic"
                desc = (sub.get("description") or f"{sub['name']} in {slug}").split(". ")[0]
                if not desc.endswith("."):
                    desc += "."
                skills_list.append({
                    "id": sub_id,
                    "name": sub["name"],
                    "description": desc[:160],
                    "aliases": [sub["name"]],
                    "evidence_criteria": [
                        f"Demonstrates usage or implementation of {sub['name']}."
                    ],
                    "sources": [{
                        "source": "roadmap.sh",
                        "topic_id": sub["id"],
                        "url": f"https://roadmap.sh/{slug}",
                        "revision": "latest"
                    }]
                })
        seen = set()
        unique_skills = []
        for s in skills_list:
            if s["id"] not in seen:
                seen.add(s["id"])
                unique_skills.append(s)

        catalog = {
            "schema_version": 1,
            "id": f"roadmap-{slug}",
            "version": "1.0.0",
            "skills": unique_skills,
        }
        validate_catalog(catalog)
        compact_tree = {
            "title": tree.get("title", slug),
            "topics": [
                {
                    "name": t["name"],
                    "subtopics": [s["name"] for s in t.get("subtopics", [])],
                }
                for t in tree.get("topics", [])
            ],
        }
        return catalog, [s["id"] for s in unique_skills], compact_tree

    raise AnalysisError(
        "unknown_skill",
        f"Skill '{skill}' not found in reference catalogs",
        400,
    )


def prepare_repository_analysis(
    bundle: dict,
    catalog: dict,
    max_input_chars: int,
    *,
    skill: str | None = None,
    allowed_skill_ids: list[str] | None = None,
    tree: dict | None = None,
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
    valid_ids = allowed_skill_ids if allowed_skill_ids is not None else [item["id"] for item in catalog["skills"]]
    assessment = _object_schema(
        {
            "decision": {
                "type": "string",
                "enum": ["supported", "insufficient_evidence", "catalog_gap"],
            },
            "skill_id": {
                "type": ["string", "null"],
                "enum": [*valid_ids, None],
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
    user_payload = {"catalog": catalog, "bundle": bundle}
    if skill:
        user_payload["target_skill"] = skill
    if tree:
        user_payload["competency_tree"] = tree

    request = {
        "messages": [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": canonical(user_payload),
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
    catalog, allowed_skill_ids, tree = resolve_skill_catalog(request.skill, settings)
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
            bundle,
            catalog,
            settings.limits.max_input_chars,
            skill=request.skill,
            allowed_skill_ids=allowed_skill_ids,
            tree=tree,
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
