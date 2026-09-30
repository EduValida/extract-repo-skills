# Repository contribution skill mapping

## Objective

Map a contributor's actual GitHub repository work to an explicit, versioned skill
catalog. Every assignment must include verifiable contribution evidence and an
explanation of how that evidence satisfies the catalog entry's criteria. Repository
dependencies, file extensions, and technology names can inform candidate selection;
they cannot establish a skill assignment on their own.

Course syllabi are outside this module's scope. The initial implementation does not
assign proficiency levels or infer that someone lacks an unobserved skill.

## Reference catalog

The catalog is a curated vocabulary, not free-form model output. Entries have stable
internal IDs, names, descriptions, aliases, explicit evidence criteria, and one or
more external topic references. Each reference records its source, topic ID, URL,
and immutable source revision. The catalog also has its own ID and version.
Published catalog versions are immutable; changes require a new version. Reports
record a canonical JSON SHA-256 digest as well as the catalog ID and version.

roadmap.sh is the first reference candidate. Its topics guide vocabulary selection;
our evidence criteria describe what can be demonstrated through contributions.
Descriptions and criteria are project-authored. Other reference sources may be
added. Equivalent topics should map to one internal entry with multiple references.
Related but distinct topics must remain separate. Roadmap ordering does not imply
skill inheritance, prerequisites demonstrated, role completion, or proficiency.

New entries and changes to criteria require catalog review outside extraction.
An extractor can only emit IDs already present in the selected catalog version.

## Assessment contract

Each report records its schema version, exact catalog identity, repository
identifier, contributor email, and a list of assessments. Each assessment records:

- A decision: `supported`, `insufficient_evidence`, or `catalog_gap`.
- A catalog skill ID for the first two decisions; `null` for a catalog gap.
- A rationale explaining the decision in relation to the contribution and criteria.
- One or more evidence references, each containing a full commit SHA, repository
  relative file path, diff side (`before` or `after`), and inclusive line range.

An empty assessment list means nothing has been assessed. It is not a negative
evaluation. A catalog gap records work that cannot be represented by the current
catalog; its rationale is not a new skill label. Insufficient evidence means that
the cited contribution does not justify the specified skill, not that the person
lacks the skill.

Evidence uses old-file lines for removals and new-file lines for additions. Every
cited line must be changed by that commit. Explanations may discuss surrounding
context, but context alone is not attributable evidence. Initial Git verification
requires an exact author-email match and rejects merge commits and coauthored
commits because attribution needs further review. Root commits are supported.
Commit authorship is a recorded attribution, not proof of identity or original work.

Validation can check catalog membership and citation provenance. It cannot prove
that a change demonstrates a skill; this remains a reviewer or extractor judgment.
Generated, vendored, copied, and mechanical changes must be considered during that
judgment. A syntactically valid assignment is not automatically a correct one.

## Initial implementation

Provide dependency-free Python catalog and report validation, a small starter
catalog, a CLI, and optional read-only citation verification against a local Git
checkout. The starter catalog is deliberately limited and requires domain review
before use in evaluations. No network or model calls occur during validation.

Commands:

- `catalog`: validate and display catalog entries and the exact catalog identity.
- `validate-report`: validate a manually authored or extractor-produced report;
  with `--repo`, also check cited changes and commit attribution in local Git.

Validation without `--repo` must explicitly report that Git evidence was not
verified. The caller is responsible for choosing the checkout corresponding to the
report's repository identifier; this version does not authenticate remote identity.

## Next milestones

1. Review and broaden the reference subset; manually annotate 20–30 contributions,
   including CSS-only work in Django repositories, documentation, tests, dependency
   updates, and generated code. Keep fixtures separate from real evaluation data.
2. Collect bounded contributor changes from local Git and retain relevant context.
3. Retrieve candidate catalog entries, evaluate evidence using a baseline and an
   LLM-based experiment, and validate all outputs against the same contract.
4. Evaluate unsupported assignments, incorrect citations, missed supported skills,
   appropriate abstention, and catalog coverage separately. Candidate retrieval
   accuracy and final assignment accuracy must also be measured separately.

The first end-to-end milestone is a reviewable report for one contributor in one
repository, with only catalog skill IDs and traceable supporting evidence.
