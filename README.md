# extract-repo-skills

Part of GT-EduValida. Map a contributor's Git repository changes to a versioned skill
catalog, with verifiable evidence for each assignment. See the [project spec](docs/spec.md).

## Current functionality

- Validate a catalog with stable skill IDs, aliases, evidence criteria, and versioned
  external references.
- Validate reports against an exact catalog ID, version, and SHA-256 digest.
- Distinguish supported skills, insufficient evidence, and catalog coverage gaps.
- Optionally verify commit authorship and cited changed lines in a local Git checkout.
- Collect one commit and ask an LLM to propose evidence-linked catalog assessments.
- Resolve an explicit GitHub commit or single-commit PR URL, or a local commit.
- Preview model input and validate generated reports before saving them.
- Analyze multiple commits for explicitly selected author emails through FastAPI,
  using five static artifacts from local or remote Git repositories without forge APIs.

Reports can be authored manually or proposed by an LLM through `analyze`.
The first model adapter uses DeepSeek, with a configurable model ID. LLM conclusions
require human review. Validation checks structure and
citation provenance, not whether the cited change truly demonstrates the skill.
Graph-based candidate retrieval and evaluation on human-reviewed real
contributions remain future work.

The four-entry [starter catalog](catalogs/roadmap-starter.json), version 0.2.0,
references CSS media queries, Flexbox, Grid, and Rust from roadmap.sh at a pinned
source commit. Its descriptions and evidence criteria are project-authored and
need domain review. It is not a comprehensive skills reference.

Rust uses the ID `rust.programming`. Its criteria require evidence of Rust-language
behavior in the contribution itself. An accepted PR in a Rust repository, unchanged
Rust files, or edits limited to prose or unrelated configuration do not establish
this skill. File extensions are not decisive: a meaningful Rust code example in
documentation can qualify, while a comment-only change in a `.rs` file may not.

The original [CSS catalog](catalogs/roadmap-css-starter.json), ID
`roadmap-css-starter`, version 0.1.0, remains unchanged for old reports. The expanded
catalog has ID `roadmap-starter`; use the exact catalog referenced by each report.

## Repository/user API (phase 1)

Requires Python 3.14+, Git and the external `cloc` executable. Object reading uses
pygit2/libgit2; language byte context uses Pygments, line counts use cloc, and
function metrics use Lizard (installed with the Python dependencies).
The API extra adds FastAPI/Uvicorn; normal inference also needs the
LLM extra and `DEEPSEEK_API_KEY`. Preview needs no model credentials.

```sh
uv sync --extra api --extra llm --extra dev
```

Install cloc with your OS package manager, or install the tested 2.10 release into
the virtual environment (requires Perl). Python dependency installation does not
install this external executable:

```sh
curl --fail --silent --show-error --location https://raw.githubusercontent.com/AlDanial/cloc/v2.10/cloc --output .venv/bin/cloc
chmod +x .venv/bin/cloc
.venv/bin/cloc --version
```

The service searches PATH, then beside the running Python executable. Use
`EXTRACT_CLOC_EXECUTABLE=/absolute/path/to/cloc` for another server installation.
Missing cloc returns `503 cloc_unavailable`; it does not silently omit metrics.
The existing single-commit CLI does not require cloc.

Configure registered local repositories or allowed remote hosts in `.env`, using
the absolute paths appropriate to your server:

```dotenv
EXTRACT_LOCAL_REPOSITORIES='{"demo":"/tmp/skill-demo/repo"}'
EXTRACT_REMOTE_HOSTS=git.example.org,github.com,gitlab.com
EXTRACT_TIMEOUT_SECONDS=120
EXTRACT_MAX_CONCURRENCY=2
EXTRACT_MAX_INPUT_CHARS=60000
```

Create the existing synthetic demo if that destination is new:

```sh
uv run python examples/create_demo.py /tmp/skill-demo
uv run --extra api --extra llm uvicorn extract_repo_skills.api:create_app --factory --env-file .env --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` to inspect the request/response schema. Preview
the exact artifacts and model input:

```sh
curl --fail --silent --show-error http://127.0.0.1:8000/analyses \
  -H 'Content-Type: application/json' \
  -d '{"repository":"local:demo","user":{"emails":["demo@example.invalid"]},"dry_run":true}'
```

For a normal assessment, set `dry_run` to `false` and configure the model key.
`repository` can also be an HTTPS or SSH Git URL, for example
`https://git.example.org/team/project.git` or
`ssh://git@git.example.org/team/project.git`. Enable its hostname in
`EXTRACT_REMOTE_HOSTS`. HTTPS access is public; SSH uses the server's
noninteractive identity. GitHub/GitLab APIs, platform logins, embedded credentials,
arbitrary client filesystem paths and SCP-style URLs are not part of this API.

Only `repository` and `user.emails` are required. `ref` defaults to HEAD and
`max_commits` defaults to 50 (maximum 100). Selection uses descending author date
with SHA as a tie-breaker within the resolved reference's reachable history.
Commits on unrelated branches are not automatically included. Several exact
author emails may be associated explicitly with the same user; this is recorded
Git attribution, not proof of personal identity or original work.

The five artifacts are Git log, per-commit diffs, language byte percentages,
project structure/manifests, and contribution context. The manifesto records
SHAs, hashes, tool versions, selected/excluded commits, limits and coverage.
Language percentages and unchanged project context cannot support skill citations.
Pygments classification/exclusions are explicit heuristics and are not equivalent
to Linguist. The touched-files summary counts whole files at HEAD, not authored
bytes. No repository code, tests, hooks or build/install scripts are executed.

`languages.json.line_counts` adds cloc counts of code, comments and blank lines,
including code-line percentages and a separate touched-snapshot-files summary.
These percentages use a different denominator from Pygments byte percentages.
Counts include duplicate-content files and record original paths/blob IDs;
unrecognized files are listed explicitly. Tool timing and temporary paths are
removed so repeated previews remain reproducible.

`contribution-context.json.function_metrics` adds Lizard metrics for functions
intersecting changed lines and their baseline counterparts: code lines, cyclomatic
complexity, tokens and parameters. Before/after records identify commit, snapshot,
blob, file, function and line range. Deltas are computed only for unique same-name
functions in the same path; ambiguous or unmatched functions have no delta.
Unsupported languages, absent files and no detected functions have distinct statuses.
Source suppression/generated-code directives cannot hide functions from the collector.
All metrics are context, and neither volume nor complexity establishes a skill.
Both tools' versions/configuration are recorded; the current artifact policy and
prompt are version 2. cloc runs on isolated copies of selected UTF-8 Git blobs,
with an empty configuration and a 30-second process timeout. Lizard parses text
in memory. Source files are never imported or executed. The five-artifact count,
complete-input budget and existing citation rules remain intact.

Merges, coauthorship, binary/non-UTF-8 changes, symlinks, submodules and changes
without citable text require review and exclude the whole commit, with a reason.
Excluded commits occupy a selection slot. Size/history limits fail before
inference without silent truncation. Shallow repositories are unsupported; the
new object reader currently supports SHA-1 repositories. See the spec for all
limits and artifact policies.

Responses have `status` equal to `preview`, `completed`, or
`no_eligible_contributions`. Completed responses include a version-2 report and
run metadata, with validated catalog IDs and changed-line evidence. No eligible
contributions return an empty report without calling the LLM. Reports remain
proposals requiring human review. The API does not persist report files; save the
response yourself when needed. Existing CLI validation supports both versions.

Each analysis runs in a separate process and is terminated on timeout. The
concurrency setting applies per Uvicorn worker; excess requests receive 429.
Structured errors distinguish invalid input, source access, collection limits,
provider rejection and timeout. `GET /health` checks the service without remote
or model calls. Serve this API within your intended access boundary; authentication
and a durable job queue are not included in phase 1.

Run the regression and API tests (LLM responses are simulated):

```sh
uv run --extra api --extra llm --extra dev python -m unittest discover -s tests -v
```

The reference graph and competency/technology records are scheduled for phase 2
in the [architecture plan](docs/architecture-plan.md).

## Run the existing CLI

Requires Python 3.14+ and Git for evidence verification. With uv:

```sh
uv run extract-repo-skills catalog catalogs/roadmap-starter.json
```

Create a synthetic repository and manually annotated report in a **new directory**:

```sh
uv run python examples/create_demo.py /tmp/skill-demo
uv run extract-repo-skills validate-report /tmp/skill-demo/report.json \
  --catalog catalogs/roadmap-starter.json --repo /tmp/skill-demo/repo
```

The demo covers all three assessment decisions. It is a fixture, not evaluation
data or an automatically extracted report. Choose another destination if it exists.

Omit `--repo` to check the report contract only; the output explicitly reports that
Git evidence was not verified. With `--repo`, use the checkout corresponding to
the report's repository identifier. The CLI does not authenticate remote identity.
Git verification accepts only exact author-email matches and changed-line citations;
merges and commits with coauthor trailers require review and are rejected for now.
Both added and removed lines can be cited. Unchanged context cannot be evidence.

For an existing Python 3.14 environment with the project's dependencies installed,
the same commands can run without uv:

```sh
PYTHONPATH=src python3.14 -m extract_repo_skills catalog catalogs/roadmap-starter.json
PYTHONPATH=src python3.14 examples/create_demo.py /tmp/skill-demo
```

## Analyze a contribution

Use `analyze` for the normal workflow. After configuring the API key below:

```sh
uv run --extra llm extract-repo-skills analyze https://github.com/OWNER/REPO/pull/123
uv run --extra llm extract-repo-skills analyze https://github.com/OWNER/REPO/commit/COMMIT_SHA
uv run --extra llm extract-repo-skills analyze COMMIT_SHA --repo /path/to/local/checkout
```

The command uses the bundled `roadmap-starter` catalog (0.2.0), derives the
contributor email from the selected commit's recorded author, and prints a summary.
It saves the validated report and run metadata under `reports/`, using a unique
filename. That directory is ignored by Git. `--catalog`, `--contributor-email`,
and `--output` allow explicit overrides. An author override must match the commit;
the PR opener and the local Git user are not used for attribution.

URL mode supports public GitHub contributions. It resolves the specific commit
through the GitHub API, fetches that commit and its parent into a temporary Git
repository, and removes it when finished. It does not check out or run source code.
Only the selected contribution's changes are assessed. With `--repo`, the resolved
commit must already exist locally; the URL still requires the GitHub API. Private
contributions can be analyzed using a SHA and a local checkout.

A PR must currently contain exactly one commit. The original PR commit is analyzed,
not the merge commit. Multi-commit PRs are rejected; analyze each commit separately
until attribution and aggregation across commits are implemented.

Preview the input without calling the LLM:

```sh
uv run extract-repo-skills analyze HEAD --repo /tmp/skill-demo/repo --dry-run
```

Local previews are offline. URL previews still need GitHub access to retrieve the
contribution. `--dry-run` prints JSON to stdout and does not save a report.

### Configuration and debug commands

The existing `analyze-commit`, `catalog`, and `validate-report` commands remain
available with their existing options and JSON output for debugging.

First inspect exactly what would be supplied to the model. This command uses no
credentials, optional SDK, or network. It resolves `HEAD` to a full commit SHA,
checks the specified author, and prepares the catalog, patches, and output schema:

```sh
uv run extract-repo-skills analyze-commit \
  --repo /tmp/skill-demo/repo --commit HEAD \
  --contributor-email demo@example.invalid \
  --catalog catalogs/roadmap-starter.json --dry-run
```

To request a real assessment, install the optional SDK and configure a DeepSeek API
key in `.env`. Copy `.env.example` to `.env` if you do not already have one, then
fill it in:

```dotenv
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY
DEEPSEEK_MODEL=deepseek-flash
```

The CLI uses `python-dotenv` to load `.env` from the current working directory
before resolving the model. Run commands from this project's root when using its
`.env`. It does not search parent directories or load a file from `--repo`.
Exported environment variables take precedence. The debug command `analyze-commit`
also accepts `--model`, which overrides both the environment and `.env`.
`.env` and its local variants are ignored by Git;
`.env.example` contains no credentials and is tracked.

The default model is `deepseek-flash`; `DEEPSEEK_MODEL` selects a different DeepSeek
model with JSON Output support. For explicit debug configuration:

```sh
uv sync --extra llm

uv run --extra llm extract-repo-skills analyze-commit \
  --repo /tmp/skill-demo/repo --commit HEAD \
  --contributor-email demo@example.invalid \
  --catalog catalogs/roadmap-starter.json \
  --output /tmp/skill-demo/llm-report.json
```

`--model` overrides `DEEPSEEK_MODEL`. The live command sends the catalog, commit
message, changed file paths, patches, and surrounding context to the configured
DeepSeek endpoint (`https://api.deepseek.com`). It makes one request with a 60-second
timeout, automatic retries disabled, thinking disabled, and a default output limit
of 8,000 tokens. The adapter passes `DEEPSEEK_API_KEY` explicitly to the compatible
`openai` SDK; it does not use an OpenAI key or endpoint. `python-dotenv` is a core
dependency; the SDK is in the optional `llm` extra.

The adapter follows [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
and [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/),
using the compatible Python client illustrated in the DeepSeek documentation.
JSON mode requests valid JSON; the output schema is supplied in the prompt and
its constraints are enforced locally. Validation checks catalog membership,
decision consistency, exact commit scope, author, file paths, and changed lines.
Refusals, incomplete responses, malformed output, and invalid citations fail the
command without writing a report. Unsupported API models produce an error.

With `--output /tmp/skill-demo/llm-report.json`, the command creates two new files:

- `llm-report.json`: the existing report format, accepted by `validate-report`.
- `llm-report.json.run.json`: requested and returned model IDs, response ID, token
  usage, prompt version, input digest, commit, catalog identity, and review status.

Existing output files are never overwritten. With `analyze-commit` and no `--output`,
the report goes to stdout and run metadata goes to stderr; `analyze` saves files
automatically and prints a summary. Human review is required even when all
citations validate. The input digest identifies the request; it does not imply
that repeated LLM calls produce identical conclusions.

This first version processes the entire supplied catalog and all text changes in
one commit, with five lines of context around each hunk. It reads committed content
only. It rejects commits with more than 50 changed paths and input over 60,000
characters instead of silently truncating them. `--max-input-chars` and
`--max-output-tokens` on `analyze-commit` adjust the character and output-token limits; the character
limit is not an exact token estimate. Empty commits, binary changes, mode-only or
empty-file changes, symlinks, submodules, merges, and coauthorship require manual
review. A mixed commit containing unsupported changes is also rejected in full.
Generated or copied text is not automatically detected; the model is instructed to
consider it, and its judgment must be reviewed.

To evaluate a contribution without Rust code in a Rust repository, use the expanded
catalog and the SHA of the original PR commit (or the single-parent squash commit),
not a merge commit with two parents:

```sh
uv run --extra llm extract-repo-skills analyze-commit \
  --repo /path/to/rust-repository --commit YOUR_COMMIT_SHA \
  --contributor-email your-author-email@example.com \
  --catalog catalogs/roadmap-starter.json --output rust-assessment.json
```

For the simpler workflow, use `analyze YOUR_COMMIT_SHA --repo /path/to/rust-repository`
or the single-commit PR URL; the bundled catalog already includes Rust.

If the contribution demonstrates no Rust-language behavior, the expected result is
no supported `rust.programming` assignment. The model may omit that unrelated skill,
mark it `insufficient_evidence` if considered relevant, or identify a catalog gap
for the actual work. This is an expectation for semantic evaluation, not a guarantee
enforced by file-extension rules or by citation validation.

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
uv run --extra llm python -m unittest discover -s tests -v
```

Tests use temporary local Git repositories and mocked provider responses; they do
not require network or model access after dependencies are installed. Without the
`llm` extra, SDK adapter tests are skipped and the core tests still run. These tests
verify integration behavior, not the semantic accuracy of any model.
