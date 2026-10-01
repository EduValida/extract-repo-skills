"""Create a synthetic Git repository and manually annotated report for the CLI."""

import argparse
import json
import subprocess
from pathlib import Path

from extract_repo_skills.contracts import catalog_identity, read_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New directory to create")
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    repo = destination / "repo"
    repo.mkdir()

    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *arguments],
            text=True,
        ).strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Synthetic Demo Contributor")
    git("config", "user.email", "demo@example.invalid")
    (repo / "style.css").write_text(".card { display: block; }\n", encoding="utf-8")
    git("add", "style.css")
    git("commit", "-q", "-m", "Add a baseline stylesheet")
    (repo / "style.css").write_text(
        ".card { display: block; }\n@media (max-width: 40rem) {\n  .card { width: 100%; }\n}\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("Open index.html in a browser to preview the page.\n", encoding="utf-8")
    git("add", "style.css", "README.md")
    git("commit", "-q", "-m", "Adapt card width on small screens and document previewing")
    commit = git("rev-parse", "HEAD")
    catalog_path = Path(__file__).resolve().parents[1] / "catalogs/roadmap-starter.json"
    catalog = read_json(catalog_path)
    css_evidence = {"commit": commit, "path": "style.css", "side": "after", "start_line": 2, "end_line": 4}
    report = {
        "schema_version": 1,
        "catalog": catalog_identity(catalog),
        "repository": repo.as_uri(),
        "contributor_email": "demo@example.invalid",
        "assessments": [
            {
                "decision": "supported",
                "skill_id": "css.media-queries",
                "rationale": "The new viewport condition makes cards use full width on screens up to 40rem.",
                "evidence": [css_evidence],
            },
            {
                "decision": "insufficient_evidence",
                "skill_id": "css.flexbox",
                "rationale": "These changes adjust width under a media condition and provide no evidence of flex layout behavior.",
                "evidence": [css_evidence],
            },
            {
                "decision": "catalog_gap",
                "skill_id": None,
                "rationale": "The added preview instructions are outside this catalog of CSS and Rust skills; no skill is assigned.",
                "evidence": [{"commit": commit, "path": "README.md", "side": "after", "start_line": 1, "end_line": 1}],
            },
        ],
    }
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic repository: {repo}")
    print(f"Manually annotated report: {destination / 'report.json'}")


if __name__ == "__main__":
    main()
