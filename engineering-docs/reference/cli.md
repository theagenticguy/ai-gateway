# ai-gateway · CLI reference

The repository ships one command-line tool: **`admin-cli`**, a thin operator client for the AI Gateway control plane, kept as a standalone `uv` project under `clients/admin_cli/`. It is not part of the Lambda runtime — it has its own `pyproject.toml`, does not import the gateway `src/` code, and does not reimplement the server Pydantic models. It forwards JSON bodies to the admin REST API and prints the parsed response envelope. `clients/admin_cli/admin_cli/__init__.py:1` The design is deliberate: a CLI before a UI, validating the API contract a future admin UI will consume. `clients/admin_cli/README.md:10`

Operators here are the engineers who run the gateway internally for their own org's teams — the CLI administers the teams, budgets, routing configs, and pricing overrides those teams are governed by. It is built on [cyclopts](https://cyclopts.readthedocs.io/) (the command tree) and [httpx](https://www.python-httpx.org/) (HTTP + Cognito M2M auth). `clients/admin_cli/pyproject.toml:7`

The command tree maps 1:1 onto the control-plane admin routes documented in [reference/public-api](public-api.md); every verb below is cross-linked to the handler that serves it.

## Installation & entry point

The console script `admin-cli` is registered against `admin_cli.__main__:app.meta` — the meta-app, which carries the global `--json` flag and the clean-error handler. `clients/admin_cli/pyproject.toml:12` The same target is reachable as `python -m admin_cli`; `__main__.py` re-exports `app`/`main` and calls `main()` under `__main__`. `clients/admin_cli/admin_cli/__main__.py:15` Python 3.13+ is required. `clients/admin_cli/pyproject.toml:6`

```bash
cd clients/admin_cli
uv sync
uv run admin-cli --help
uv run admin-cli teams --help
```

`main()` calls `app.meta()`, which — per cyclopts' default `result_action` — raises `SystemExit` with the launcher's return code rather than returning; callers must not wrap it in another `sys.exit`. `clients/admin_cli/admin_cli/commands.py:299`

## Authentication

The CLI authenticates as a Cognito **machine-to-machine** client using the OAuth `client_credentials` grant. On its first call it POSTs `grant_type=client_credentials&scope=<admin scope>` to the Cognito token endpoint with `Authorization: Basic base64(client_id:client_secret)`, caches the access token in-memory for the process (Cognito TTL 3600s), attaches it as `Authorization: Bearer …` on each admin-API call, and — on a `401` — drops the token, re-fetches once, and retries the request exactly once. `clients/admin_cli/admin_cli/client.py:171` `clients/admin_cli/admin_cli/client.py:200`

The requested scope defaults to `https://gateway.internal/admin` (`DEFAULT_ADMIN_SCOPE`), the same canonical admin scope the control plane enforces (`gwcore` `ADMIN_SCOPE`, see [reference/public-api](public-api.md)). `clients/admin_cli/admin_cli/client.py:32` The client credentials **must** hold this admin scope: team clients are provisioned with `invoke` only and receive `403 forbidden` from every admin endpoint, so they cannot drive this CLI. `clients/admin_cli/README.md:42`

### Configuration (env vars)

Nothing is hardcoded — connection and credential values come from the environment, each with an equivalent flag that wins over the env var. Resolution and the "which var is missing" error message live in `Config.from_env`. `clients/admin_cli/admin_cli/client.py:83`

| Env var | Required | Purpose |
|---|---|---|
| `GATEWAY_ADMIN_URL` | yes | Admin REST API stage invoke URL, e.g. `https://{api_id}.execute-api.{region}.amazonaws.com/{env}` (Terraform `admin_api` output `api_url`). |
| `GATEWAY_CLIENT_ID` | yes | Cognito M2M client id (an admin-scoped client). |
| `GATEWAY_CLIENT_SECRET` | yes | Cognito M2M client secret. |
| `GATEWAY_TOKEN_ENDPOINT` | yes\* | Full Cognito token URL: `https://{domain}.auth.{region}.amazoncognito.com/oauth2/token`. |
| `COGNITO_DOMAIN` + `AWS_REGION` | yes\* | Alternative to `GATEWAY_TOKEN_ENDPOINT`: the endpoint is derived from these (`AWS_DEFAULT_REGION` also accepted). |
| `GATEWAY_ADMIN_SCOPE` | no | Override the requested scope (default `https://gateway.internal/admin`). |

\* Provide **either** `GATEWAY_TOKEN_ENDPOINT` **or** `COGNITO_DOMAIN` + `AWS_REGION`; the endpoint is derived from the latter pair when the explicit URL is absent. `clients/admin_cli/admin_cli/client.py:74` A trailing slash on `GATEWAY_ADMIN_URL` is stripped. `clients/admin_cli/admin_cli/client.py:124` When any required value is missing, the CLI exits non-zero with a `config_error` naming the missing var — never a stack trace. `clients/admin_cli/admin_cli/client.py:119`

## Global flags & output

The meta-app exposes one global flag, `--json`, which emits raw compact JSON (`{"a":1}`) instead of the default pretty-printed, key-sorted output. `clients/admin_cli/admin_cli/commands.py:274` It precedes the sub-app on the command line (e.g. `admin-cli --json teams list`). Output goes to stdout via `_print`; an empty response body (e.g. a `204`) prints nothing. `clients/admin_cli/admin_cli/commands.py:116`

Mutating commands accept a request body through two combinable flags, built by `build_body`: `clients/admin_cli/admin_cli/commands.py:79`

- `--body` / `-b` — inline JSON, or `@path/to/file.json` to read the body from a file.
- `--set` / `-s` — set a top-level field as `key=value` (repeatable). The value is parsed as JSON when it parses (`1000` → int, `true` → bool), else kept as a string; `--set` overlays fields on top of `--body`.

The CLI does not validate the body — the server owns the schema and returns a `validation_failed` error envelope if it is wrong. `clients/admin_cli/README.md:76`

## Command reference

Each sub-app is registered on the root app; each verb is a cyclopts command whose docstring names the HTTP method and path it calls. `clients/admin_cli/admin_cli/commands.py:62` Positional arguments below are path segments; `--body`/`--set` apply to the create/update/upsert verbs.

### `teams` — team registration

`clients/admin_cli/admin_cli/commands.py:58`

| Command | Positional args | Body | HTTP call | Handler |
|---|---|---|---|---|
| `teams list` | — | — | `GET /teams` | `src/team_registration/handler.py:56` |
| `teams get <team_id>` | `team_id` | — | `GET /teams/{id}` | `src/team_registration/handler.py:58` |
| `teams create` | — | `--body` / `--set` (team_name, contact_email, tier, description) | `POST /teams` | `src/team_registration/handler.py:54` |
| `teams rotate <team_id>` | `team_id` | — | `POST /teams/{id}/rotate` | `src/team_registration/handler.py:60` |
| `teams delete <team_id>` | `team_id` | — | `DELETE /teams/{id}` | `src/team_registration/handler.py:62` |

`teams create` registers a team (creates a Cognito client, stores metadata, seeds a budget); `teams rotate` rotates the team's Cognito client credentials; `teams delete` deactivates the team and deletes its Cognito client, revoking its tokens. `clients/admin_cli/admin_cli/commands.py:146`

### `budgets` — budgets (paginated)

`clients/admin_cli/admin_cli/commands.py:59`

| Command | Positional args | Options / body | HTTP call | Handler |
|---|---|---|---|---|
| `budgets list` | — | `--cursor <C>` (opaque `next_cursor` from a prior list) | `GET /budgets` | `src/budget_admin/handler.py:90` |
| `budgets get <budget_id>` | `budget_id` | — | `GET /budgets/{id}` | `src/budget_admin/handler.py:112` |
| `budgets create` | — | `--body` / `--set` (scope, scope_id, budget_usd, …) | `POST /budgets` | `src/budget_admin/handler.py:93` |
| `budgets update <budget_id>` | `budget_id` | `--body` / `--set` (budget_usd, period, …) | `PUT /budgets/{id}` | `src/budget_admin/handler.py:114` |
| `budgets delete <budget_id>` | `budget_id` | — | `DELETE /budgets/{id}` | `src/budget_admin/handler.py:116` |

`budgets list` is cursor-paginated: the `--cursor` value is passed as the `cursor` query param and is dropped when `None`, so an empty cursor is omitted. `clients/admin_cli/admin_cli/commands.py:179` `clients/admin_cli/admin_cli/client.py:241`

### `routing` — routing configs

`clients/admin_cli/admin_cli/commands.py:60`

| Command | Positional args | Body | HTTP call | Handler |
|---|---|---|---|---|
| `routing list` | — | — | `GET /routing/configs` | `src/routing_config/handler.py:284` |
| `routing get <name>` | `name` | — | `GET /routing/configs/{name}` | `src/routing_config/handler.py:286` |
| `routing create` | — | `--body` / `--set` (strategy, targets, metadata) | `POST /routing/configs` | `src/routing_config/handler.py:288` |
| `routing update <name>` | `name` | `--body` / `--set` | `PUT /routing/configs/{name}` | `src/routing_config/handler.py:290` |
| `routing delete <name>` | `name` | — | `DELETE /routing/configs/{name}` | `src/routing_config/handler.py:292` |

`clients/admin_cli/admin_cli/commands.py:212`

### `pricing` — pricing overrides

`clients/admin_cli/admin_cli/commands.py:61`

| Command | Positional args | Body | HTTP call | Handler |
|---|---|---|---|---|
| `pricing list` | — | — | `GET /pricing` | `src/pricing_admin/handler.py:260` |
| `pricing get <provider> <model>` | `provider`, `model` | — | `GET /pricing/{provider}/{model}` | `src/pricing_admin/handler.py:262` |
| `pricing upsert <provider> <model>` | `provider`, `model` | `--body` / `--set` (input_per_1k, …) | `PUT /pricing/{provider}/{model}` | `src/pricing_admin/handler.py:264` |
| `pricing delete <provider> <model>` | `provider`, `model` | — | `DELETE /pricing/{provider}/{model}` | `src/pricing_admin/handler.py:266` |

`pricing list` returns the DynamoDB overrides merged over the static pricing table; `pricing delete` reports whether a static fallback price remains. `clients/admin_cli/admin_cli/commands.py:245`

## Errors & exit codes

The response envelope is parsed by the client: a success body is returned as a dict (empty body → `None`), while a 4xx/5xx maps the gateway error envelope `{"error": {"code", "message", details?}}` to a `GatewayError`. `clients/admin_cli/admin_cli/client.py:263` The meta-app launcher catches `GatewayError` and prints a readable one-liner — `error: <code>: <message>` (plus ` (details)` when present) — to stderr, returning exit code `1`; a successful run returns `0`. `clients/admin_cli/admin_cli/commands.py:290` No stack trace is printed for a config, auth, or API error. The error `code` is the same stable machine-readable code the control plane emits (`config_error`, `token_error`, `body_error` locally; `validation_failed`, `not_found`, `conflict`, `forbidden`, `upstream_error`, etc. from the server — see the `ControlPlaneError` hierarchy in [reference/public-api](public-api.md)).

## Examples

```bash
# Configure once (M2M admin client + Cognito domain)
export GATEWAY_ADMIN_URL="https://abc123.execute-api.us-east-1.amazonaws.com/prod"
export GATEWAY_CLIENT_ID="…"  GATEWAY_CLIENT_SECRET="…"
export COGNITO_DOMAIN="my-gateway"  AWS_REGION="us-east-1"

# Register a team with inline --set fields (POST /teams)
uv run admin-cli teams create \
  --set team_name=payments-svc \
  --set contact_email=payments@example.com \
  --set tier=premium

# Create a budget from a file, then page through budgets
uv run admin-cli budgets create --body @budget.json
uv run admin-cli budgets list --cursor "<next_cursor from a prior list>"

# Upsert a pricing override, raw compact JSON output (--json is global, precedes the sub-app)
uv run admin-cli --json pricing upsert bedrock claude-sonnet-4 \
  --set input_per_1k=0.003 --set output_per_1k=0.015

# Rotate a team's credentials (POST /teams/{id}/rotate)
uv run admin-cli teams rotate team-123
```

## Testing

The CLI is tested with no live network or AWS: `tests/test_client.py` uses stdlib `httpx.MockTransport` to serve both the Cognito token endpoint and the admin API, covering token acquisition, envelope parsing, the 401→refresh→retry path, and the error-envelope → `GatewayError` mapping. `clients/admin_cli/tests/test_client.py:34` `tests/test_commands.py` drives the cyclopts command tree with token lists and stubs `_client`, asserting each verb's method/path/body/params and the non-zero exit on a `GatewayError`. `clients/admin_cli/tests/test_commands.py:108`

## See also

- [reference/public-api](public-api.md) — the control-plane HTTP routes every command calls, and the `gwcore` `ADMIN_SCOPE` / `ControlPlaneError` surface the CLI mirrors.
- [insights/contract-map](../insights/contract-map.md) — the HTTP response and error envelopes the CLI parses.
- [architecture/module-map](../architecture/module-map.md) — the `src/` service packages behind each admin route.
</content>
</invoke>
