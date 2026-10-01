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

The expanded `roadmap-starter` catalog (0.2.0) includes the original CSS entries
and `rust.programming`, referenced to the Rust language-basics topic. Rust criteria
require the contribution itself to demonstrate language behavior. A merged PR in
a Rust repository is not evidence by itself. Meaningful Rust examples in documentation
can qualify; an `.rs` extension alone cannot. The original `roadmap-css-starter`
catalog (0.1.0) remains available unchanged to validate earlier reports.

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
- `analyze-commit`: collect one attributed commit, request a semantic assessment,
  and validate the resulting report. `--dry-run` previews model input offline.

Validation without `--repo` must explicitly report that Git evidence was not
verified. The caller is responsible for choosing the checkout corresponding to the
report's repository identifier; this version does not authenticate remote identity.

## Single-commit semantic assessment

The first extractor collects all changed regular text files in one commit with
five context lines around each hunk. It resolves a supplied revision to an immutable
SHA before inference. Shared Git helpers ensure collection and verification use
the same attribution rules and diff settings. Only committed content is collected;
untracked files and working-tree modifications are excluded.

The entire catalog, commit message, patches, and explicit changed-line intervals
are supplied to the model. Repository content is untrusted data, not instructions.
The prompt asks for substantive evidence, explicitly permits abstention, and rules
out technology-stack attribution, inherited skills, and proficiency labels.

The first provider adapter uses DeepSeek Chat Completions through the compatible,
optional OpenAI Python SDK. It requests JSON Output and supplies the assessment
schema in the prompt; JSON mode itself does not enforce that schema. The default
model is `deepseek-flash`, configurable through `--model` or `DEEPSEEK_MODEL`;
the caller configures `DEEPSEEK_API_KEY`. The client explicitly targets
`https://api.deepseek.com`. There are no automatic retries, requests time out after
60 seconds, thinking is disabled, and output has a configurable token limit.
Other providers can supply the same
assessment object to the provider-independent validation layer.

The model supplies only the assessments array. Application code fixes repository,
contributor, and catalog metadata. The output schema describes allowed skill IDs, commit,
paths, sides, and decision values. Local validation checks the entire report and
every citation, including a check that evidence belongs to the single requested
commit. No report is accepted on refusal, incomplete output, malformed JSON,
invented IDs, or unverifiable citations. Semantic correctness remains unproven
until human review and evaluation.

The CLI emits a version-1 report and separate run metadata, preserving compatibility
with manually authored reports. Metadata records provider/model, response ID,
usage, prompt version, input digest, resolved commit, catalog identity, and the
need for human review. It does not persist raw model input automatically. Neither
catalog digests nor input digests guarantee deterministic model behavior.

The initial limits are 50 changed paths and 60,000 characters for the complete
model input, including the catalog, instructions, and output schema. Character
limits are not token counts. Oversized inputs fail before inference rather than
being silently truncated. Unsupported changes (binary, mode-only, empty-file,
symlink, or submodule changes) make the entire commit require manual review.
Empty commits are rejected. Text that is generated, vendored, or copied still
requires semantic review; automatic origin detection is not implemented.

## Next milestones

1. Review and broaden the reference subset; manually annotate 20–30 contributions,
   including CSS-only work in Django repositories, documentation, tests, dependency
   updates, and generated code. Keep fixtures separate from real evaluation data.
2. Evaluate the single-commit LLM flow against the reviewed examples and a simple
   baseline, tracking errors without relying on schema validity as a quality score.
3. Extend collection to bounded commit ranges and add candidate retrieval as the
   catalog grows; preserve evidence and attribution during aggregation.
4. Evaluate unsupported assignments, incorrect citations, missed supported skills,
   appropriate abstention, and catalog coverage separately. Candidate retrieval
   accuracy and final assignment accuracy must also be measured separately.

The first end-to-end milestone is a reviewable report for one contributor in one
repository, with only catalog skill IDs and traceable supporting evidence.
