# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi", "uvicorn"]
# ///
"""Mock webhook adapter for the agentgateway data-plane PoC.

Stands in for the real adapter that fronts the budget_enforcement and
content_scanner Lambdas. It speaks agentgateway's guardrail-webhook contract
(receives {body:{messages}}, returns {action: pass|mask|reject}) and simulates
the Lambda's {verdict} decision via env flags, so you can exercise allow and
deny paths without AWS.

Run standalone:  uv run mock_adapter.py
Or via compose:  docker compose up  (see Dockerfile.mock-adapter)

Env:
  MOCK_BUDGET_VERDICT  "true"|"false"  (false -> 429 reject)
  MOCK_SCAN_VERDICT    "true"|"false"  (false -> content block)
  MOCK_RETRY_AFTER     seconds for the budget reject body
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, Request

app = FastAPI(title="agentgateway-poc-mock-adapter")


def _bool_env(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).lower() == "true"


@app.post("/budget/request")
async def budget_request(req: Request) -> dict:
    """Translate agentgateway -> (simulated) budget_enforcement -> agentgateway.

    The real adapter would invoke the budget_enforcement Lambda with
    ``{"jwt_token": jwt, "model": ..., "estimated_tokens": ...}`` and read back
    its ``{"verdict", "data", "error"}`` shape. Here the verdict is faked via env.
    """
    agw_body = await req.json()  # {"body": {"messages": [...]}}
    jwt = req.headers.get("x-amzn-oidc-data", "")  # agentgateway forwards headers
    logger_fields = {"messages": len(agw_body.get("body", {}).get("messages", [])), "has_jwt": bool(jwt)}
    print(f"budget/request {logger_fields}")
    verdict = _bool_env("MOCK_BUDGET_VERDICT")
    if verdict:
        return {"action": {"pass": {}}}
    retry = int(os.environ.get("MOCK_RETRY_AFTER", "172800"))
    return {
        "action": {
            "reject": {
                "status_code": 429,
                "body": json.dumps(
                    {"error": "Monthly budget exceeded (103.2% of $1000)", "retry_after_seconds": retry}
                ),
                "reason": "Monthly budget exceeded",
            }
        }
    }


@app.post("/scan/request")
async def scan_request(req: Request) -> dict:
    """Translate agentgateway -> (simulated) content_scanner -> agentgateway."""
    await req.json()
    verdict = _bool_env("MOCK_SCAN_VERDICT")
    if verdict:
        return {"action": {"pass": {}}}
    return {
        "action": {
            "reject": {
                "status_code": 400,
                "body": json.dumps({"error": "content policy violation"}),
                "reason": "content_scanner blocked",
            }
        }
    }


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8088)  # noqa: S104
