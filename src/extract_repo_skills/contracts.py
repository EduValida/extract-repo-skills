"""Strict, versioned JSON contracts shared by reviewers and future extractors."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


class ValidationError(ValueError):
    """A catalog, report, or evidence reference violates the contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _object(value: object, fields: set[str], context: str) -> None:
    _require(isinstance(value, dict), f"{context}: expected an object")
    _require(set(value) == fields, f"{context}: expected exactly {sorted(fields)}")


def _text(value: object, context: str) -> None:
    _require(isinstance(value, str) and bool(value.strip()), f"{context}: expected nonempty text")


def _items(value: object, context: str, *, nonempty: bool = True) -> None:
    _require(isinstance(value, list), f"{context}: expected an array")
    if nonempty:
        _require(bool(value), f"{context}: must not be empty")


def _id(value: object, context: str) -> None:
    _text(value, context)
    _require(bool(re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", value)), f"{context}: invalid ID")


def _version(value: object, context: str) -> None:
    _require(type(value) is int and value == 1, f"{context}: only schema version 1 is supported")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, f"JSON: duplicate key {key!r}")
        result[key] = value
    return result


def parse_json(text: str) -> dict:
    def reject_constant(value: str) -> None:
        raise ValidationError(f"JSON: invalid constant {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON: {exc.msg}") from exc


def read_json(path: str | Path) -> dict:
    try:
        return parse_json(Path(path).read_text(encoding="utf-8"))
    except UnicodeError as exc:
        raise ValidationError(f"Invalid UTF-8 JSON in {path}") from exc


def validate_catalog(catalog: dict) -> dict:
    _object(catalog, {"schema_version", "id", "version", "skills"}, "catalog")
    _version(catalog["schema_version"], "catalog.schema_version")
    _id(catalog["id"], "catalog.id")
    _text(catalog["version"], "catalog.version")
    _items(catalog["skills"], "catalog.skills")
    ids = set()
    for index, skill in enumerate(catalog["skills"]):
        context = f"catalog.skills[{index}]"
        _object(skill, {"id", "name", "description", "aliases", "evidence_criteria", "sources"}, context)
        _id(skill["id"], f"{context}.id")
        _require(skill["id"] not in ids, f"{context}: duplicate skill ID {skill['id']}")
        ids.add(skill["id"])
        for key in ("name", "description"):
            _text(skill[key], f"{context}.{key}")
        for key in ("aliases", "evidence_criteria"):
            _items(skill[key], f"{context}.{key}", nonempty=key != "aliases")
            for item in skill[key]:
                _text(item, f"{context}.{key}")
        _items(skill["sources"], f"{context}.sources")
        for source in skill["sources"]:
            _object(source, {"source", "topic_id", "url", "revision"}, f"{context}.sources")
            for key, value in source.items():
                _text(value, f"{context}.sources.{key}")
            try:
                url = urlparse(source["url"])
            except ValueError as exc:
                raise ValidationError(f"{context}: invalid source URL") from exc
            _require(url.scheme == "https" and bool(url.netloc), f"{context}: source URL must use HTTPS")
    return catalog


def catalog_identity(catalog: dict) -> dict:
    validate_catalog(catalog)
    canonical = json.dumps(catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "id": catalog["id"],
        "version": catalog["version"],
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _validate_v1_report(report: dict, catalog: dict) -> dict:
    identity = catalog_identity(catalog)
    _object(report, {"schema_version", "catalog", "repository", "contributor_email", "assessments"}, "report")
    _version(report["schema_version"], "report.schema_version")
    _object(report["catalog"], {"id", "version", "sha256"}, "report.catalog")
    _require(report["catalog"] == identity, "report.catalog: catalog ID, version, or digest does not match")
    for key in ("repository", "contributor_email"):
        _text(report[key], f"report.{key}")
    _items(report["assessments"], "report.assessments", nonempty=False)
    skill_ids = {skill["id"] for skill in catalog["skills"]}
    for index, assessment in enumerate(report["assessments"]):
        context = f"report.assessments[{index}]"
        _object(assessment, {"decision", "skill_id", "rationale", "evidence"}, context)
        decision = assessment["decision"]
        _require(decision in ("supported", "insufficient_evidence", "catalog_gap"), f"{context}: unknown decision")
        if decision == "catalog_gap":
            _require(assessment["skill_id"] is None, f"{context}: catalog gaps cannot assign a skill")
        else:
            _id(assessment["skill_id"], f"{context}.skill_id")
            _require(assessment["skill_id"] in skill_ids, f"{context}: unknown skill ID {assessment['skill_id']}")
        _text(assessment["rationale"], f"{context}.rationale")
        _items(assessment["evidence"], f"{context}.evidence")
        for evidence in assessment["evidence"]:
            _object(evidence, {"commit", "path", "side", "start_line", "end_line"}, f"{context}.evidence")
            _text(evidence["commit"], f"{context}.evidence.commit")
            _require(bool(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", evidence["commit"])), f"{context}: expected full commit SHA")
            _text(evidence["path"], f"{context}.evidence.path")
            path = PurePosixPath(evidence["path"])
            _require(
                not path.is_absolute() and ".." not in path.parts
                and path.as_posix() == evidence["path"] and path.as_posix() != "."
                and "\x00" not in evidence["path"],
                f"{context}: expected a normalized repository relative path",
            )
            _require(evidence["side"] in ("before", "after"), f"{context}: invalid diff side")
            start, end = evidence["start_line"], evidence["end_line"]
            _require(type(start) is int and type(end) is int, f"{context}: line numbers must be integers")
            _require(1 <= start <= end, f"{context}: invalid line range")
    return report


def validate_report(report: dict, catalog: dict) -> dict:
    """Accept legacy single-email reports and explicit repository/user reports."""
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 2:
        return _validate_v1_report(report, catalog)
    _object(report, {"schema_version", "catalog", "repository", "user", "reference", "selection", "assessments"}, "report")
    user = report["user"]
    _object(user, {"emails", "attribution"}, "report.user")
    _items(user["emails"], "report.user.emails")
    for email in user["emails"]:
        _text(email, "report.user.emails")
        _require(bool(re.fullmatch(r"[^\s@]{1,200}@[^\s@]{1,200}", email)), "report.user: invalid author email")
    _require(len(user["emails"]) <= 20 and len(set(user["emails"])) == len(user["emails"]), "report.user: invalid email selection")
    _require(user["attribution"] == "exact_recorded_author_email", "report.user: unsupported attribution policy")
    reference = report["reference"]
    _object(reference, {"requested", "resolved"}, "report.reference")
    _text(reference["requested"], "report.reference.requested")

    def sha(value):
        _require(isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)), "Expected a full commit SHA")

    sha(reference["resolved"])
    selection = report["selection"]
    _object(selection, {"max_commits", "order", "reachable_commits", "matching_commits", "considered_commits",
                        "omitted_by_commit_limit", "selected", "excluded", "coverage_complete"}, "report.selection")
    for key in ("max_commits", "reachable_commits", "matching_commits", "considered_commits", "omitted_by_commit_limit"):
        _require(type(selection[key]) is int and selection[key] >= 0, "report.selection: invalid count")
    _require(1 <= selection["max_commits"] <= 100, "report.selection: invalid max_commits")
    _require(selection["order"] == "author_timestamp_desc_then_sha_asc", "report.selection: invalid ordering")
    _require(selection["matching_commits"] <= selection["reachable_commits"], "report.selection: invalid coverage counts")
    _require(selection["considered_commits"] == min(selection["matching_commits"], selection["max_commits"]), "report.selection: invalid considered count")
    _require(selection["omitted_by_commit_limit"] == selection["matching_commits"] - selection["considered_commits"], "report.selection: invalid omitted count")
    _items(selection["selected"], "report.selection.selected", nonempty=False)
    _items(selection["excluded"], "report.selection.excluded", nonempty=False)
    _require(len(selection["selected"]) + len(selection["excluded"]) == selection["considered_commits"], "report.selection: missing decisions")
    ids = set()
    selected_ids = set()
    for item in selection["selected"]:
        _object(item, {"commit", "author_email"}, "report.selection.selected")
        sha(item["commit"])
        _text(item["author_email"], "report.selection.author_email")
        _require(item["author_email"] in user["emails"], "report.selection: author outside requested identities")
        _require(item["commit"] not in ids, "report.selection: duplicate commit")
        ids.add(item["commit"])
        selected_ids.add(item["commit"])
    for item in selection["excluded"]:
        _object(item, {"commit", "reason"}, "report.selection.excluded")
        sha(item["commit"])
        _text(item["reason"], "report.selection.excluded.reason")
        _require(item["commit"] not in ids, "report.selection: duplicate commit")
        ids.add(item["commit"])
    complete = selection["omitted_by_commit_limit"] == 0 and not selection["excluded"]
    _require(type(selection["coverage_complete"]) is bool and selection["coverage_complete"] == complete,
             "report.selection: inconsistent coverage flag")
    _validate_v1_report({"schema_version": 1, "catalog": report["catalog"],
                         "repository": report["repository"], "contributor_email": user["emails"][0],
                         "assessments": report["assessments"]}, catalog)
    for assessment in report["assessments"]:
        for evidence in assessment["evidence"]:
            _require(evidence["commit"] in selected_ids, "Citation is outside the analyzed selection")
    return report
