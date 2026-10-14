"""One bounded analysis per process; HTTP owns the temporary workspace lifetime."""

import json
import sys
from pathlib import Path

from .artifacts import CollectionLimits
from .contracts import ValidationError
from .repository import AnalysisError
from .repository_analysis import (
    RepositoryAnalysisRequest,
    ServiceSettings,
    analyze_repository,
)


def main():
    try:
        payload = json.loads(sys.stdin.read(65_537))
        raw_settings = payload["settings"]
        limits = CollectionLimits(**raw_settings.pop("limits"))
        settings = ServiceSettings(**raw_settings, limits=limits)
        request = RepositoryAnalysisRequest.from_dict(payload["request"])
        result = analyze_repository(request, settings, Path(sys.argv[1]))
        reply = {"result": result}
    except AnalysisError as exc:
        reply = {"error": {"code": exc.code, "message": str(exc), "status": exc.status}}
    except ValidationError, OSError, ValueError:
        reply = {
            "error": {
                "code": "analysis_failed",
                "message": "Analysis configuration or repository data is invalid",
                "status": 422,
            }
        }
    except Exception:  # noqa: BLE001 -- process boundary must not expose private diagnostics
        reply = {
            "error": {
                "code": "internal_error",
                "message": "Analysis could not be completed",
                "status": 500,
            }
        }
    print(json.dumps(reply, ensure_ascii=False))


if __name__ == "__main__":
    main()
