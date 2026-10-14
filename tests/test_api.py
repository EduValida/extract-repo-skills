import asyncio
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from test_contracts import CATALOG_PATH
from test_git_evidence import GitRepositoryFixture

from extract_repo_skills.api import create_app, run_worker
from extract_repo_skills.artifacts import ARTIFACT_NAMES, CollectionLimits
from extract_repo_skills.repository import AnalysisError
from extract_repo_skills.repository_analysis import (
    RepositoryAnalysisRequest,
    ServiceSettings,
    analyze_repository,
)


class ApiTests(GitRepositoryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.settings = ServiceSettings(
            local_repositories={"fixture": str(self.repo)},
            catalog_path=str(CATALOG_PATH),
        )
        self.body = {
            "repository": "local:fixture",
            "user": {"emails": [self.report["contributor_email"]]},
        }

    def client(self, provider=None, settings=None):
        async def runner(request, configured):
            return analyze_repository(
                RepositoryAnalysisRequest.from_dict(request),
                configured,
                Path(self.temp.name),
                provider=provider,
            )

        return TestClient(create_app(settings or self.settings, runner=runner))

    def test_real_worker_preview_and_health_without_credentials_or_llm(self):
        with TestClient(create_app(self.settings)) as client:
            health = client.get("/health")
            self.assertEqual(health.json(), {"status": "ok"})
            response = client.post("/analyses", json={**self.body, "dry_run": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "preview")
            self.assertEqual(
                set(response.json()["bundle"]["artifacts"]), set(ARTIFACT_NAMES)
            )
            self.assertNotIn("report", response.json())
            schema = client.get("/openapi.json").json()
            self.assertIn("/analyses", schema["paths"])
            self.assertIn("AnalysisRequest", schema["components"]["schemas"])

    def test_http_assessment_with_fake_llm_validates_and_returns_v2(self):
        provider = Mock(
            return_value=(
                {"assessments": self.report["assessments"]},
                {"provider": "test"},
            )
        )
        with self.client(provider) as client:
            response = client.post("/analyses", json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report"]["schema_version"], 2)
        self.assertEqual(result["report"]["assessments"], self.report["assessments"])
        self.assertTrue(result["run"]["human_review_required"])
        self.assertTrue(result["run"]["git_evidence_verified"])
        provider.assert_called_once()

    def test_invalid_assessment_returns_no_report_or_source_text(self):
        provider = Mock(side_effect=ValueError("unused"))
        provider.side_effect = AnalysisError("private", "PRIVATE SOURCE TEXT")
        with self.client(provider) as client:
            response = client.post("/analyses", json=self.body)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "assessment_rejected")
        self.assertNotIn("PRIVATE", response.text)
        self.assertNotIn("report", response.json())

    def test_no_eligible_contribution_is_explicit_and_does_not_call_llm(self):
        provider = Mock()
        with self.client(provider) as client:
            response = client.post(
                "/analyses",
                json={**self.body, "user": {"emails": ["absent@example.invalid"]}},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "no_eligible_contributions")
        self.assertEqual(response.json()["report"]["assessments"], [])
        provider.assert_not_called()

    def test_unknown_repository_disallowed_host_invalid_ref_and_input_limit(self):
        with self.client() as client:
            for body, status, code in (
                (
                    {**self.body, "repository": "local:missing"},
                    404,
                    "unknown_repository",
                ),
                (
                    {**self.body, "repository": "https://git.example.org/repo.git"},
                    403,
                    "remote_host_not_allowed",
                ),
                ({**self.body, "repository": "/etc"}, 422, "invalid_source"),
                ({**self.body, "ref": "missing"}, 422, "invalid_ref"),
            ):
                with self.subTest(body=body):
                    response = client.post("/analyses", json=body)
                    self.assertEqual(response.status_code, status, response.text)
                    self.assertEqual(response.json()["error"]["code"], code)
        with self.client(
            settings=replace(self.settings, limits=CollectionLimits(max_input_chars=50))
        ) as client:
            response = client.post("/analyses", json={**self.body, "dry_run": True})
        self.assertEqual(response.status_code, 413)
        self.assertNotIn("report", response.json())

    def test_request_rejects_coercion_unknown_fields_and_invalid_identities(self):
        with self.client() as client:
            for body in (
                {},
                {**self.body, "max_commits": "5"},
                {**self.body, "max_commits": True},
                {**self.body, "dry_run": "yes"},
                {**self.body, "extra": "field"},
                {**self.body, "user": {"emails": ["Display Name"]}},
                {**self.body, "user": {"emails": ["a@b", "a@b"]}},
                {**self.body, "user": {"emails": [], "login": "user"}},
                {**self.body, "ref": "-argument"},
            ):
                with self.subTest(body=body):
                    response = client.post("/analyses", json=body)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(
                        response.json()["error"]["code"], "invalid_request"
                    )

    def test_concurrency_limit_rejects_extra_analysis_but_health_stays_available(self):
        async def scenario():
            started, release = asyncio.Event(), asyncio.Event()

            async def runner(_request, _settings):
                started.set()
                await release.wait()
                return {"status": "preview", "bundle": {}}

            app = create_app(replace(self.settings, max_concurrency=1), runner=runner)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                first = asyncio.create_task(client.post("/analyses", json=self.body))
                await started.wait()
                second = await client.post("/analyses", json=self.body)
                self.assertEqual(second.status_code, 429)
                self.assertEqual((await client.get("/health")).status_code, 200)
                release.set()
                self.assertEqual((await first).status_code, 200)

        asyncio.run(scenario())

    def test_worker_timeout_kills_process_and_cleans_workspace(self):
        async def scenario():
            create_process = asyncio.create_subprocess_exec
            processes, workspaces = [], []

            async def slow_process(*args, **kwargs):
                workspaces.append(Path(args[-1]))
                process = await create_process(
                    sys.executable, "-c", "import time; time.sleep(30)", **kwargs
                )
                processes.append(process)
                return process

            with (
                patch(
                    "extract_repo_skills.api.asyncio.create_subprocess_exec",
                    side_effect=slow_process,
                ),
                self.assertRaises(AnalysisError) as caught,
            ):
                await run_worker(self.body, replace(self.settings, timeout_seconds=1))
            self.assertEqual(caught.exception.code, "analysis_timeout")
            self.assertEqual(caught.exception.status, 504)
            self.assertIsNotNone(processes[0].returncode)
            self.assertFalse(workspaces[0].exists())

        asyncio.run(scenario())

    def test_environment_configuration_does_not_need_forge_settings(self):
        with patch.dict(
            "os.environ",
            {
                "EXTRACT_LOCAL_REPOSITORIES": json.dumps({"fixture": str(self.repo)}),
                "EXTRACT_REMOTE_HOSTS": "git.example.org, gitlab.example.org",
            },
        ):
            settings = ServiceSettings.from_env()
        self.assertEqual(settings.local_repositories, {"fixture": str(self.repo)})
        self.assertEqual(
            settings.allowed_remote_hosts, ("git.example.org", "gitlab.example.org")
        )
