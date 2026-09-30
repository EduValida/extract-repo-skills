"""Command line interface for catalog and evidence-linked report validation."""

import argparse
import json
import sys
from collections import Counter

from .contracts import ValidationError, catalog_identity, read_json, validate_catalog, validate_report
from .git_evidence import verify_git_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    catalog_parser = commands.add_parser("catalog", help="Validate and display a reference catalog")
    catalog_parser.add_argument("catalog", help="Catalog JSON file")
    report_parser = commands.add_parser("validate-report", help="Validate catalog assignments and evidence references")
    report_parser.add_argument("report", help="Report JSON file")
    report_parser.add_argument("--catalog", required=True, help="Exact reference catalog JSON file")
    report_parser.add_argument("--repo", help="Local Git checkout for citation and authorship verification")
    args = parser.parse_args()
    try:
        catalog = validate_catalog(read_json(args.catalog))
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
