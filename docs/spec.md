# Repository contribution skill mapping

## Objective

Map a contributor's actual Git repository work to an explicit, versioned skill
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

The default catalog is bundled as `src/extract_repo_skills/data/roadmap-starter.json`
so installed commands work outside the project checkout. It must match the reviewed
`catalogs/roadmap-starter.json`; a test checks this equality. Catalog updates must
update both copies and respect version immutability.

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

## Repository/user service (phase 1)

The new primary API analyzes a contributor across a bounded set of commits in a
Git repository, independently of GitHub, GitLab, or another hosting platform.
The existing contribution-first CLI and version-1 reports remain supported.
The phase-2 reference graph and structured competency records are planned in
`docs/architecture-plan.md`; they are not part of this implementation.

### Inputs and attribution

`POST /analyses` accepts `repository`, `user.emails`, and optional `ref`,
`max_commits` (default 50, range 1–100), and `dry_run` (default false).
Emails are explicit, unique identities matched exactly against recorded commit
authors. A name, committer, forge login, or `.mailmap` does not implicitly change
attribution. This matching records Git authorship; it does not authenticate a
person, prove email ownership, or establish original work.

`local:NAME` refers to an absolute repository path registered by the server.
The HTTP caller cannot request arbitrary local paths. Remotes use HTTPS or SSH
Git URLs; their hostnames must be enabled in the server configuration. Embedded
passwords/tokens and arbitrary Git protocols are not accepted. HTTPS remotes in
this version are public; SSH can use the server's configured noninteractive
identity. No forge API is called, and SSH/HTTPS access remains a Git operation.
Nondefault ports and SCP-style SSH URLs are outside the initial input contract.

Without `ref`, use the local HEAD or the remote's advertised HEAD. Resolve it to
an immutable commit before collection. Remotes are fetched into a temporary bare
repository, including HEAD and branch history, without tags, checkout, recursive
submodules, repository execution or automatic LFS downloads. Shallow histories
are rejected. Only commits reachable from the resolved reference are considered.
PR-only and unrelated branch work is outside that scope.

Read Git objects through `GitRepositoryReader`, implemented with pygit2/libgit2.
Diffs use C Git with the established explicit Myers/no-renames/no-textconv policy;
pygit2 parses individual added/removed lines. Both regular and bare repositories
are supported. The new reader uses the Git object formats supported by libgit2
(SHA-1 in this release); unsupported formats fail rather than fabricate evidence.
The legacy CLI's contracts remain unchanged.

Scan at most 10,000 reachable commits. Order matching authors by descending
author timestamp and ascending SHA for ties. Consider the first `max_commits`
matches. Skip merges, coauthored commits, empty commits, and commits containing
unsupported changes, recording a reason for each. A skipped commit occupies a
slot and is not silently replaced by older work. Binary/non-UTF-8 changes,
symlinks, submodules, and changes without citable text lines require review.
Exclusion applies to the whole commit; supported paths are not silently extracted
from an otherwise unsupported commit.

### Five static artifacts

1. `git-log.json`: selected SHAs, parents, recorded author identities/times and
   messages. Messages contextualize work but cannot establish a skill.
2. `git-diff.json`: per-commit/per-file patches and inclusive changed-line ranges
   on each side. These are the only attributable citation locations.
3. `languages.json`: byte-based breakdown at the resolved snapshot, plus a
   separate breakdown of snapshot files touched in selected commits. Whole-file
   bytes are not bytes authored by the contributor; deleted files are absent from
   that snapshot summary. The denominator is classified regular UTF-8 text bytes
   after exclusions, including recognizable documentation/configuration formats.
   `line_counts` additionally records cloc code/comment/blank counts by file and
   language, with a separate touched-snapshot-files summary and percentages whose
   denominator is code lines, not bytes.
4. `project-context.json`: snapshot tree with object IDs, modes and sizes, plus
   committed manifests and build/test/CI configuration with source paths/IDs.
5. `contribution-context.json`: before/after snippets around changed lines, with
   snapshot/blob IDs and LF-based line numbers. Unchanged snippet lines are context.
   `function_metrics` records Lizard results before/after for functions intersecting
   the changed-line ranges and their same-name counterparts, with source IDs and
   function line ranges. Metrics include NLOC, cyclomatic complexity, tokens and
   parameter count. Deltas require a unique same-name match within the same path;
   ambiguous or unmatched functions have no delta and are not assumed to be
   additions, removals or refactorings. Unsupported languages, absent files and
   no detected functions are distinguished explicitly.

The MVP's language classifier uses Pygments filename/content heuristics, recording
the installed classifier version and `static-artifacts-v2` policy. It is not
equivalent to Linguist. Directory/name exclusions cover vendor, node_modules,
dist, build, target, generated, selected lockfiles, and minified JS/CSS suffixes.
These exclusions are heuristics, not proof of origin. Binary/non-UTF-8 and
unclassified files are recorded separately; `.gitattributes` overrides are not
applied. Actual generated/copied content still needs semantic review. Linguist
can later implement the classifier contract without changing artifact roles.

cloc is a separately installed server executable (tested with 2.10); its path may
be configured with `EXTRACT_CLOC_EXECUTABLE`. Missing cloc is a 503 error before
inference. The counter receives only regular UTF-8 blobs after the common path
exclusions, independently of Pygments recognition. Each blob is copied into a
separate numeric directory in the owned temporary workspace. Unsafe/non-ASCII
basename characters are replaced by underscores for cloc JSON compatibility;
original repository paths and blob IDs are restored in the result. This may affect
basename-specific classification and is recorded in the configuration.

cloc uses an empty configuration, JSON/by-file output, counts duplicate-content
files independently and disables its internal per-file timeout to avoid silent
timeout skips; the entire cloc process still has a 30-second deadline. Files not
counted by cloc are listed explicitly, and malformed/inconsistent output is
rejected. Timing, generated timestamps and temporary paths are excluded from
the persisted artifact. Byte and line classifiers may disagree; their results
remain separate and neither establishes attributable skills.

Lizard is a Python dependency and parses committed text in memory. Its collector
ignores repository `lizard forgive` and `GENERATED CODE` suppression directives,
retaining comment line positions. Partial-analysis diagnostics reject the result.
The installed version, configuration and function-matching policy are recorded.
Neither count/complexity scores nor their deltas demonstrate a catalog competency
without substantive changed-line evidence. All added metrics remain within the
existing five artifacts and complete model-input budget. The new prompt is
`repository-artifacts-v2`; report and manifest schema versions remain compatible.

A separate version-1 manifest records repository, user, requested/resolved
reference, policy/tool versions, limits, exact selection, exclusion reasons,
coverage counts and SHA-256 hashes of each canonical JSON artifact. Coverage is
complete only when no matching commit was omitted by the cap or excluded. Empty
arrays mean no content was found for that artifact/field; they are not negative
skill assessments.

Limits default to 5,000 snapshot files, 50 changed paths per commit, 1,000,000
bytes per required regular blob, 20,000,000 regular bytes per snapshot, 8,000
characters of context per file/side, and 60,000 characters for the complete model
request, including reference catalog, manifest, schema and instructions. History
messages have a separate aggregate budget of 5,000,000 characters. Limits fail
explicitly before inference and do not truncate or sample an oversized artifact.
Character budgets are not token counts.

### Inference, results and HTTP lifecycle

The service prepares all five artifacts together with the exact reviewed catalog,
assessment schema and versioned instructions. All repository content is untrusted
data. The model returns only assessments; the application fixes identity, catalog
and scope. Context/technology percentages do not demonstrate competencies.

The version-2 report contains `schema_version`, `catalog`, `repository`, `user`,
`reference`, `selection`, and `assessments`. Assessment decisions, rationale,
catalog IDs and changed-line citations retain their version-1 semantics. A citation
must refer to a selected commit by one of the requested recorded authors and cite
actual changed lines. Catalog labels are never invented. Both report versions
can be checked by `validate-report`, including `--repo` Git verification.

`dry_run` returns `status: preview`, the bundle and the complete prepared model
request, without LLM credentials/calls. Normal analysis returns `status: completed`,
the validated report and run metadata including provider/model/usage, input/bundle
digests, artifact hashes and the human-review requirement. Nothing is written to
report files automatically. No eligible commits return
`status: no_eligible_contributions`, an empty report and an explicit reason, without
calling the model. This is not a finding that the person lacks skills.

FastAPI starts a separate process per request, with a default 120-second total
deadline and a default capacity of two analyses per server process. A Git command
has a 30-second timeout. Timeouts/cancellation terminate the analysis and its
descendant processes before cleaning temporary objects. There is no durable queue
or background job state. API workers must be sized with this per-process capacity
in mind. `GET /health` does not access remotes or an LLM.

Errors use `{ "error": { "code": "...", "message": "..." } }`, with 422 for
invalid input/reference, 403 for a disabled remote host, 404 for an unregistered
local source, 413 for limits, 429 for busy service, 502 for Git retrieval or rejected
provider assessments, and 504 for timeout. Provider/Git diagnostics containing
source text or credentials are not returned. `/docs` and `/openapi.json` document
the typed request/response contracts. See README for configuration and execution.

## Legacy single-contribution implementation

Provide Python catalog and report validation, a small starter
catalog, a CLI, and optional read-only citation verification against a local Git
checkout. The starter catalog is deliberately limited and requires domain review
before use in evaluations. No network or model calls occur during validation.

Commands:

- `analyze CONTRIBUTION`: the primary workflow, accepting a GitHub commit URL,
  single-commit PR URL, or local commit SHA/revision. Select the bundled catalog,
  derive the recorded commit author, save report files, and print a summary.
- `catalog`: validate and display catalog entries and the exact catalog identity.
- `validate-report`: validate a manually authored or extractor-produced report;
  with `--repo`, also check cited changes and commit attribution in local Git.
- `analyze-commit`: collect one attributed commit, request a semantic assessment,
  and validate the resulting report. `--dry-run` previews model input offline.

The existing commands and their options remain available as debug tools.

## Contribution-first CLI

The unit of analysis is the explicitly selected contribution. The repository
locates Git objects needed for patches and evidence verification; its overall
technology stack does not establish skills.

For a local SHA or revision, `--repo` selects the checkout and defaults to the
current directory. For a public GitHub URL, resolve the immutable commit SHA
through the GitHub API. Without `--repo`, fetch that commit and its parent into a
temporary Git repository, without checking out or executing source code, and keep
the objects until evidence validation finishes. With `--repo`, use the existing
local objects. URL mode still needs the GitHub API and does not support authenticated
private repositories; use a local SHA for private contributions.

PR URLs initially support exactly one original PR commit. Do not substitute the
merge commit or silently select one commit from a multi-commit PR. Multi-commit PRs
fail with instructions to analyze commits individually. Range aggregation remains
a separate milestone.

Derive contributor email from the commit author, not the PR opener or local Git
configuration. An explicit `--contributor-email` asserts an expected author and
must pass the same attribution checks. `--catalog` overrides the bundled reference.
Model selection uses `DEEPSEEK_MODEL`; explicit provider and limit controls remain
in `analyze-commit`.

The primary command saves reports to unique files in `reports/` by default, or to
an explicit `--output`, without overwriting existing files. Print supported skill
names and rationales, counts for insufficient evidence and catalog gaps, output
paths, and the need for human review. Run metadata also records the original
contribution reference. `analyze-commit` retains its JSON stdout/stderr behavior.
`--dry-run` previews JSON without calling the LLM or writing report files; local
previews need no network, while URL previews still resolve and fetch the contribution.

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

The CLI uses the core dependency `python-dotenv` to load `.env` from the current
working directory before resolving settings. Existing environment variables are
not overwritten; an explicit `--model` has highest precedence. No parent-directory
search or automatic loading from the analyzed repository takes place. The local
`.env` is ignored by Git and `.env.example` documents the supported settings.

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
3. Evaluate the repository/user phase-1 flow on bounded real contribution sets.
   Add graph-based candidate retrieval in phase 2 as the catalog grows; preserve
   evidence and attribution during aggregation.
4. Evaluate unsupported assignments, incorrect citations, missed supported skills,
   appropriate abstention, and catalog coverage separately. Candidate retrieval
   accuracy and final assignment accuracy must also be measured separately.

The first end-to-end milestone is a reviewable report for one contributor in one
repository, with only catalog skill IDs and traceable supporting evidence.
