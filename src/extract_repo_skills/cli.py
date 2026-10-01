"""Command line interface for catalog and evidence-linked report validation."""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from .analysis import prepare_analysis, validate_assessment
from .contracts import ValidationError, catalog_identity, read_json, validate_catalog, validate_report
from .contribution_source import resolve_contribution
from .deepseek_provider import DEFAULT_MODEL
from .git_evidence import verify_git_evidence


DEFAULT_CATALOG = Path(__file__).parent / "data" / "roadmap-starter.json"


def _print_summary(report: dict, catalog: dict, path: Path) -> None:
    names = {skill["id"]: skill["name"] for skill in catalog["skills"]}
    supported = [item for item in report["assessments"] if item["decision"] == "supported"]
    print(f"Contributor: {report['contributor_email']}")
    if supported:
        print("Skills supported by this contribution:")
        for item in supported:
            print(f"  {names[item['skill_id']]}: {item['rationale']}")
    else:
        print("No supported skills were proposed for this contribution.")
    counts = Counter(item["decision"] for item in report["assessments"])
    print(f"Insufficient evidence: {counts['insufficient_evidence']}; catalog gaps: {counts['catalog_gap']}")
    print(f"Report: {path}\nRun metadata: {path}.run.json\nHuman review required.")


def _analyze_contribution(args: argparse.Namespace, catalog: dict) -> None:
    with resolve_contribution(args.contribution, args.repo) as source:
        args.repo = source.repo
        args.commit = source.commit
        args.repository = source.repository
        args.contributor_email = args.contributor_email or source.contributor_email
        args.source_reference = source.reference
        if args.output is None and not args.dry_run:
            directory = Path("reports")
            directory.mkdir(exist_ok=True)
            args.output = directory / f"{source.commit[:12]}-{uuid4().hex[:8]}.json"
        _analyze(args, catalog)


def _positive_int(text: str) -> int:
    number = int(text)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _analyze(args: argparse.Namespace, catalog: dict) -> None:
    run_path = Path(str(args.output) + ".run.json") if args.output else None
    if args.output and (args.output.exists() or run_path.exists()):
        raise ValidationError("Output already exists; choose a new --output path")
    if args.dry_run and args.output:
        raise ValidationError("Use stdout redirection for --dry-run; --output is for validated reports")
    if not args.dry_run and not args.model:
        raise ValidationError("Choose a model with --model or DEEPSEEK_MODEL")
    if args.output and not args.output.parent.is_dir():
        raise ValidationError("The --output parent directory must already exist")
    prepared = prepare_analysis(
        args.repo, args.commit, args.contributor_email, catalog,
        repository=args.repository, max_input_chars=args.max_input_chars,
    )
    if args.dry_run:
        print(json.dumps(prepared, indent=2, ensure_ascii=False))
        return

    from .deepseek_provider import assess

    assessment, run = assess(prepared, args.model, max_output_tokens=args.max_output_tokens)
    report = validate_assessment(assessment, prepared, catalog, args.repo)
    run["git_evidence_verified"] = True
    if getattr(args, "source_reference", None):
        run["contribution"] = args.source_reference
    if args.output:
        for path, value in ((args.output, report), (run_path, run)):
            with path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        if getattr(args, "simple_output", False):
            _print_summary(report, catalog, args.output)
        else:
            print(f"Report: {args.output}\nRun metadata: {run_path}\nHuman review required.", file=sys.stderr)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps({"run": run}, ensure_ascii=False), file=sys.stderr)


def main() -> None:
    # Use the caller's configuration, never search the repository being assessed.
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    simple_parser = commands.add_parser("analyze", help="Assess a contribution by commit SHA, commit URL, or single-commit PR URL")
    simple_parser.add_argument("contribution", help="GitHub commit/PR URL, or a local commit SHA or revision")
    simple_parser.add_argument("--repo", help="Local checkout; defaults to current directory for a local commit")
    simple_parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="Override the bundled reference catalog")
    simple_parser.add_argument("--contributor-email", help="Require this author instead of using the recorded commit author")
    simple_parser.add_argument("--output", type=Path, help="New report path; defaults to reports/COMMIT-RUN.json")
    simple_parser.add_argument("--dry-run", action="store_true", help="Preview model input without calling the LLM")
    simple_parser.set_defaults(
        model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL), max_input_chars=60_000,
        max_output_tokens=8_000, simple_output=True,
    )
    catalog_parser = commands.add_parser("catalog", help="Debug: validate and display a reference catalog")
    catalog_parser.add_argument("catalog", help="Catalog JSON file")
    report_parser = commands.add_parser("validate-report", help="Debug: validate catalog assignments and evidence references")
    report_parser.add_argument("report", help="Report JSON file")
    report_parser.add_argument("--catalog", required=True, help="Exact reference catalog JSON file")
    report_parser.add_argument("--repo", help="Local Git checkout for citation and authorship verification")
    analyze_parser = commands.add_parser("analyze-commit", help="Debug: assess one local commit with explicit configuration")
    analyze_parser.add_argument("--repo", required=True, help="Local Git checkout")
    analyze_parser.add_argument("--commit", required=True, help="Commit SHA or revision, e.g. HEAD")
    analyze_parser.add_argument("--contributor-email", required=True, help="Exact author email to assess")
    analyze_parser.add_argument("--catalog", required=True, help="Reference catalog JSON file")
    analyze_parser.add_argument("--repository", help="Report repository identifier; defaults to local checkout URI")
    analyze_parser.add_argument(
        "--model", default=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL),
        help=f"DeepSeek model; defaults to DEEPSEEK_MODEL or {DEFAULT_MODEL}",
    )
    analyze_parser.add_argument("--dry-run", action="store_true", help="Preview model input without API calls or credentials")
    analyze_parser.add_argument("--max-input-chars", type=_positive_int, default=60_000)
    analyze_parser.add_argument("--max-output-tokens", type=_positive_int, default=8_000)
    analyze_parser.add_argument("--output", type=Path, help="New report file; also creates FILE.run.json; defaults to stdout")
    args = parser.parse_args()
    try:
        catalog = validate_catalog(read_json(args.catalog))
        if args.command == "analyze":
            _analyze_contribution(args, catalog)
            return
        if args.command == "analyze-commit":
            _analyze(args, catalog)
            return
        if args.command == "catalog":
            output = {"catalog": catalog_identity(catalog), "skills": catalog["skills"]}
        else:
            report = validate_report(read_json(args.report), catalog)
            checked = verify_git_evidence(report, catalog, args.repo) if args.repo else None
            output = {
                "contract_valid": True,
                "catalog": catalog_identity(catalog),
                "decisions": dict(Counter(item["decision"] for item in report["assessments"])),
                "git_evidence_verified": args.repo is not None,
                "citations_checked": checked,
                "skill_semantics_verified": False,
            }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    except (ValidationError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
