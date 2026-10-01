import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from extract_repo_skills.cli import main
from extract_repo_skills.deepseek_provider import DEFAULT_MODEL
from test_contracts import CATALOG_PATH


class DotenvCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name) / "application"
        self.repo = Path(self.temp.name) / "repository"
        self.cwd.mkdir()
        self.repo.mkdir()
        self.arguments = [
            "extract-repo-skills", "analyze-commit", "--repo", str(self.repo),
            "--commit", "HEAD", "--contributor-email", "test@example.invalid",
            "--catalog", str(CATALOG_PATH), "--dry-run",
        ]

    def run_cli(self, environment, extra_args=()):
        observed = {}

        def capture(args, catalog):
            observed.update(model=args.model, key=os.environ.get("DEEPSEEK_API_KEY"))

        with patch.dict(os.environ, environment, clear=True), patch(
            "pathlib.Path.cwd", return_value=self.cwd,
        ), patch("sys.argv", [*self.arguments, *extra_args]), patch(
            "extract_repo_skills.cli._analyze", side_effect=capture,
        ), redirect_stdout(io.StringIO()) as stdout:
            main()
            self.assertNotIn("fixture-secret", stdout.getvalue())
        return observed

    def test_loads_dotenv_before_resolving_model(self):
        (self.cwd / ".env").write_text(
            'DEEPSEEK_API_KEY="fixture-secret"\nDEEPSEEK_MODEL=dotenv-model\n', encoding="utf-8",
        )
        self.assertEqual(self.run_cli({}), {"key": "fixture-secret", "model": "dotenv-model"})

    def test_environment_and_explicit_model_take_precedence(self):
        (self.cwd / ".env").write_text(
            "DEEPSEEK_API_KEY=dotenv-key\nDEEPSEEK_MODEL=dotenv-model\n", encoding="utf-8",
        )
        env = {"DEEPSEEK_API_KEY": "exported-key", "DEEPSEEK_MODEL": "exported-model"}
        self.assertEqual(self.run_cli(env), {"key": "exported-key", "model": "exported-model"})
        self.assertEqual(
            self.run_cli(env, ["--model", "explicit-model"]),
            {"key": "exported-key", "model": "explicit-model"},
        )

    def test_does_not_load_environment_from_target_or_parent_directory(self):
        for directory in (self.repo, self.cwd.parent):
            (directory / ".env").write_text(
                "DEEPSEEK_API_KEY=foreign-key\nDEEPSEEK_MODEL=foreign-model\n", encoding="utf-8",
            )
        self.assertEqual(self.run_cli({}), {"key": None, "model": DEFAULT_MODEL})
