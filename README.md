# extract-repo-skills

Part of GT-EduValida. Map a contributor's repository changes to a versioned skill
catalog, with verifiable evidence for each assignment. See the [project spec](docs/spec.md).

## Current functionality

- Validate a catalog with stable skill IDs, aliases, evidence criteria, and versioned
  external references.
- Validate reports against an exact catalog ID, version, and SHA-256 digest.
- Distinguish supported skills, insufficient evidence, and catalog coverage gaps.
- Optionally verify commit authorship and cited changed lines in a local Git checkout.

This is the foundation for extraction. Reports are currently authored manually;
automatic contribution collection, candidate retrieval, and model inference are
not implemented. Validation checks structure and citation provenance, not whether
the cited change truly demonstrates the skill. That judgment requires review.

The three-entry [starter catalog](catalogs/roadmap-css-starter.json) references CSS
media queries, Flexbox, and Grid from roadmap.sh at a pinned source commit. Its
descriptions and evidence criteria are project-authored and need domain review.
It demonstrates the catalog contract and is not a comprehensive skills reference.

## Run

Requires Python 3.14+ and Git for evidence verification. With uv:

```sh
uv run extract-repo-skills catalog catalogs/roadmap-css-starter.json
```

Create a synthetic repository and manually annotated report in a **new directory**:

```sh
uv run python examples/create_demo.py /tmp/skill-demo
uv run extract-repo-skills validate-report /tmp/skill-demo/report.json \
  --catalog catalogs/roadmap-css-starter.json --repo /tmp/skill-demo/repo
```

The demo covers all three assessment decisions. It is a fixture, not evaluation
data or an automatically extracted report. Choose another destination if it exists.

Omit `--repo` to check the report contract only; the output explicitly reports that
Git evidence was not verified. With `--repo`, use the checkout corresponding to
the report's repository identifier. The CLI does not authenticate remote identity.
Git verification accepts only exact author-email matches and changed-line citations;
merges and commits with coauthor trailers require review and are rejected for now.
Both added and removed lines can be cited. Unchanged context cannot be evidence.

For an existing Python 3.14 installation, the same commands can run without uv or
installing dependencies:

```sh
PYTHONPATH=src python3.14 -m extract_repo_skills catalog catalogs/roadmap-css-starter.json
PYTHONPATH=src python3.14 examples/create_demo.py /tmp/skill-demo
```

## Report format

Use the `catalog` object's exact identity from the catalog command. Each assessment
has a `decision`, `skill_id`, `rationale`, and nonempty `evidence` list. A citation has
`commit` (full SHA), `path` (repository relative), `side` (`before` or `after`),
`start_line`, and `end_line` (inclusive). `catalog_gap` requires a null `skill_id`;
other decisions require an existing catalog skill ID. The generated demo report
provides a complete example. Unknown fields and unknown skill IDs are rejected.

An insufficient-evidence decision does not mean that the contributor lacks the
skill. No proficiency level, prerequisite, or broader role is inferred.

## Tests

```sh
uv run python -m unittest discover -s tests -v
```

Tests use temporary local Git repositories and do not require network or model access.
