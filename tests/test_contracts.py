import copy
import json
import tempfile
import unittest
from pathlib import Path

from extract_repo_skills.contracts import (
    ValidationError, catalog_identity, read_json, validate_catalog, validate_report,
)


CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalogs/roadmap-starter.json"


def make_report(catalog: dict, commit: str = "a" * 40) -> dict:
    return {
        "schema_version": 1,
        "catalog": catalog_identity(catalog),
        "repository": "https://example.invalid/demo",
        "contributor_email": "contributor@example.invalid",
        "assessments": [{
            "decision": "supported",
            "skill_id": "css.media-queries",
            "rationale": "Adds a viewport condition controlling card width.",
            "evidence": [{"commit": commit, "path": "style.css", "side": "after", "start_line": 5, "end_line": 6}],
        }],
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.catalog = read_json(CATALOG_PATH)
        self.report = make_report(self.catalog)

    def test_starter_catalog_and_report(self):
        validate_catalog(self.catalog)
        validate_report(self.report, self.catalog)

    def test_legacy_reports_keep_their_original_catalog_identity(self):
        legacy = read_json(CATALOG_PATH.with_name("roadmap-css-starter.json"))
        report = make_report(legacy)
        validate_report(report, legacy)
        with self.assertRaisesRegex(ValidationError, "does not match"):
            validate_report(report, self.catalog)
        self.assertEqual(self.catalog["skills"][:-1], legacy["skills"])
        self.assertEqual(self.catalog["skills"][-1]["id"], "rust.programming")

    def test_unknown_skill_is_not_an_assignment(self):
        self.report["assessments"][0]["skill_id"] = "python.django"
        with self.assertRaisesRegex(ValidationError, "unknown skill ID"):
            validate_report(self.report, self.catalog)

    def test_same_version_with_changed_content_is_rejected(self):
        self.catalog["skills"][0]["evidence_criteria"].append("New criterion")
        with self.assertRaisesRegex(ValidationError, "digest does not match"):
            validate_report(self.report, self.catalog)

    def test_catalog_digest_ignores_object_key_order(self):
        reordered = dict(reversed(list(self.catalog.items())))
        self.assertEqual(catalog_identity(reordered), catalog_identity(self.catalog))

    def test_duplicate_ids_and_missing_source_revision_are_rejected(self):
        duplicate = copy.deepcopy(self.catalog)
        duplicate["skills"].append(copy.deepcopy(duplicate["skills"][0]))
        with self.assertRaisesRegex(ValidationError, "duplicate skill ID"):
            validate_catalog(duplicate)
        del self.catalog["skills"][0]["sources"][0]["revision"]
        with self.assertRaises(ValidationError):
            validate_catalog(self.catalog)

    def test_abstention_and_gap_are_distinct(self):
        assessment = self.report["assessments"][0]
        assessment["decision"] = "insufficient_evidence"
        validate_report(self.report, self.catalog)
        assessment["decision"] = "catalog_gap"
        with self.assertRaisesRegex(ValidationError, "cannot assign a skill"):
            validate_report(self.report, self.catalog)
        assessment["skill_id"] = None
        validate_report(self.report, self.catalog)

    def test_empty_report_is_valid_but_assignment_without_evidence_is_not(self):
        self.report["assessments"][0]["evidence"] = []
        with self.assertRaisesRegex(ValidationError, "must not be empty"):
            validate_report(self.report, self.catalog)
        self.report["assessments"] = []
        validate_report(self.report, self.catalog)

    def test_invalid_evidence_is_rejected(self):
        cases = [
            ("commit", "HEAD"), ("commit", "a" * 41), ("path", "../secret"),
            ("path", "/etc/passwd"), ("path", "./style.css"), ("path", "."),
            ("path", "style\x00.css"), ("side", "context"),
            ("start_line", True), ("start_line", 0), ("end_line", 1),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                report = copy.deepcopy(self.report)
                report["assessments"][0]["evidence"][0][key] = value
                with self.assertRaises(ValidationError):
                    validate_report(report, self.catalog)

    def test_unrecognized_fields_and_schema_versions_are_rejected(self):
        self.report["assessments"][0]["proficiency"] = "expert"
        with self.assertRaises(ValidationError):
            validate_report(self.report, self.catalog)
        del self.report["assessments"][0]["proficiency"]
        for version in (True, 2, "1"):
            self.report["schema_version"] = version
            with self.assertRaises(ValidationError):
                validate_report(self.report, self.catalog)

    def test_json_rejects_duplicate_keys_and_nonstandard_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for text in ('{"id":"one","id":"two"}', '{"value":NaN}'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValidationError):
                    read_json(path)
            path.write_text(json.dumps(self.catalog), encoding="utf-8")
            self.assertEqual(read_json(path), self.catalog)
