"""FastAPI facade: static Git evidence collection in short-lived worker processes."""

import asyncio
import json
import os
import signal
import sys
from dataclasses import asdict
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .repository import AnalysisError
from .repository_analysis import RepositoryAnalysisRequest, ServiceSettings


class UserSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    emails: list[str] = Field(min_length=1, max_length=20)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    repository: str = Field(min_length=1, max_length=2048)
    user: UserSelection
    ref: str = Field(default="HEAD", min_length=1, max_length=256)
    max_commits: int = Field(default=50, ge=1, le=100)
    dry_run: bool = False

    @model_validator(mode="after")
    def check_git_request(self):
        RepositoryAnalysisRequest.from_dict(self.model_dump())
        return self


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["preview", "completed", "no_eligible_contributions"]
    bundle: dict | None = None
    prepared: dict | None = None
    report: dict | None = None
    run: dict | None = None


async def run_worker(request: dict, settings: ServiceSettings) -> dict:
    with TemporaryDirectory(prefix="extract-repo-skills-analysis-") as directory:
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "extract_repo_skills.worker",
                directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise AnalysisError(
                "worker_unavailable", "Cannot start the analysis process", 503
            ) from exc
        encoded = json.dumps({"request": request, "settings": asdict(settings)}).encode(
            "utf-8"
        )
        if len(encoded) > 65_536:
            process.kill()
            await process.wait()
            raise AnalysisError(
                "configuration_limit",
                "Worker request exceeds the configuration limit",
                413,
            )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(encoded), settings.timeout_seconds
            )
        except (TimeoutError, asyncio.CancelledError) as exc:
            # Stop the worker and its Git/SSH descendants before removing the workspace.
            if process.returncode is None:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise AnalysisError(
                "analysis_timeout", "Analysis exceeded the total time limit", 504
            ) from exc
        if process.returncode or len(stdout) > 2_000_000:
            raise AnalysisError(
                "worker_failed", "Analysis process did not return a bounded result", 500
            )
        try:
            reply = json.loads(stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AnalysisError(
                "worker_failed", "Analysis process returned invalid output", 500
            ) from exc
        if "error" in reply:
            error = reply["error"]
            raise AnalysisError(error["code"], error["message"], error["status"])
        return reply["result"]


def create_app(settings: ServiceSettings | None = None, *, runner=None) -> FastAPI:
    settings = settings or ServiceSettings.from_env()
    runner = runner or run_worker
    app = FastAPI(
        title="Repository contribution skill analysis",
        version="0.1.0",
        description="Five static Git artifacts with evidence-linked catalog assessments.",
    )
    capacity = asyncio.Semaphore(settings.max_concurrency)

    @app.exception_handler(AnalysisError)
    async def analysis_error(_request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Invalid analysis request",
                    "details": [
                        {"location": list(item["loc"]), "message": item["msg"]}
                        for item in exc.errors()
                    ],
                }
            },
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post(
        "/analyses",
        response_model=AnalysisResponse,
        response_model_exclude_none=True,
        responses={
            403: {"description": "Remote host is not enabled"},
            404: {"description": "Local repository is not registered"},
            413: {"description": "Collection or model input limit exceeded"},
            429: {"description": "Analysis concurrency limit reached"},
            502: {"description": "Git retrieval or assessment failed"},
            504: {"description": "Git or total analysis timeout"},
        },
    )
    async def analyze(request: AnalysisRequest):
        if capacity.locked():
            raise AnalysisError(
                "busy", "Analysis concurrency limit reached; try again later", 429
            )
        async with capacity:
            return await runner(request.model_dump(), settings)

    return app
