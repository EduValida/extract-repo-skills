"""Command line interface for catalog and evidence-linked report validation."""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from .analysis import prepare_analysis, validate_assessment
from .contracts import ValidationError, catalog_identity, read_json, validate_catalog, validate_report
from .deepseek_provider import DEFAULT_MODEL
from .git_evidence import verify_git_evidence


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
    if args.output:
        for path, value in ((args.output, report), (run_path, run)):
            with path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        print(f"Report: {args.output}\nRun metadata: {run_path}\nHuman review required.", file=sys.stderr)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps({"run": run}, ensure_ascii=False), file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    catalog_parser = commands.add_parser("catalog", help="Validate and display a reference catalog")
    catalog_parser.add_argument("catalog", help="Catalog JSON file")
    report_parser = commands.add_parser("validate-report", help="Validate catalog assignments and evidence references")
    report_parser.add_argument("report", help="Report JSON file")
    report_parser.add_argument("--catalog", required=True, help="Exact reference catalog JSON file")
    report_parser.add_argument("--repo", help="Local Git checkout for citation and authorship verification")
    analyze_parser = commands.add_parser("analyze-commit", help="Propose catalog skills for one commit using an LLM")
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
