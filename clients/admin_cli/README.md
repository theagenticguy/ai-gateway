# AI Gateway admin CLI

A **thin** operator CLI that wraps the AI Gateway control-plane admin API
(`/teams`, `/budgets`, `/routing`, `/pricing`) behind Cognito **M2M
`client_credentials`** auth. It stops operators from hand-rolling `curl` + JWT.

It is a **standalone** [`uv`](https://docs.astral.sh/uv/) project (its own
`pyproject.toml`, not part of the Lambda runtime deps). It does **not** import
the gateway `src/` code and does **not** reimplement the server Pydantic models
— it forwards JSON bodies and prints the parsed response envelope. CLI before UI,
deliberately: it validates the API contract a future admin UI will consume.

Built with [cyclopts](https://cyclopts.readthedocs.io/) (command tree) and
[httpx](https://www.python-httpx.org/) (HTTP + auth).

## Install / run

```bash
cd clients/admin_cli
uv sync
uv run admin-cli --help
uv run admin-cli teams --help
```

## Configuration (env vars)

Nothing is hardcoded — all connection and credential values come from the
environment (each has an equivalent flag; flags win):

| Env var | Required | Purpose |
|---|---|---|
| `GATEWAY_ADMIN_URL` | yes | Admin REST API stage invoke URL, e.g. `https://{api_id}.execute-api.{region}.amazonaws.com/{env}` (Terraform `admin_api` output `api_url`). |
| `GATEWAY_CLIENT_ID` | yes | Cognito M2M client id (an **admin-scoped** client). |
| `GATEWAY_CLIENT_SECRET` | yes | Cognito M2M client secret. |
| `GATEWAY_TOKEN_ENDPOINT` | yes* | Full Cognito token URL: `https://{domain}.auth.{region}.amazoncognito.com/oauth2/token` (Terraform `auth` output `cognito_token_endpoint`). |
| `COGNITO_DOMAIN` + `AWS_REGION` | yes* | Alternative to `GATEWAY_TOKEN_ENDPOINT`: the endpoint is derived from these. |
| `GATEWAY_ADMIN_SCOPE` | no | Override the requested scope (default `https://gateway.internal/admin`). |

\* Provide **either** `GATEWAY_TOKEN_ENDPOINT` **or** `COGNITO_DOMAIN` +
`AWS_REGION`.

### Admin scope requirement

The client credentials **must** hold the `https://gateway.internal/admin` scope.
Use the pool's `gateway_m2m` client (or a dedicated admin client). **Team
clients are provisioned with `invoke` only** and will get a `403 forbidden` from
every admin endpoint — they cannot drive this CLI.

The CLI requests `grant_type=client_credentials&scope=https://gateway.internal/admin`
against the token endpoint (`Authorization: Basic base64(client_id:client_secret)`),
caches the access token in-memory (Cognito TTL 3600s), attaches it as
`Authorization: Bearer …` on API calls, and **refreshes once on a 401** before
retrying.

## Command tree

Verbs map 1:1 to the admin API:

```
admin-cli teams   list | get <id> | create | rotate <id> | delete <id>
admin-cli budgets list [--cursor C] | get <id> | create | update <id> | delete <id>
admin-cli routing list | get <name> | create | update <name> | delete <name>
admin-cli pricing list | get <provider> <model> | upsert <provider> <model> | delete <provider> <model>
```

Global flag: `--json` emits raw compact JSON (default is pretty-printed).

### Bodies

Create/update/upsert commands take a JSON body via either flag (combinable):

- `--body '<json>'` — inline JSON, **or** `--body @path/to/file.json` to read a file.
- `--set key=value` — set/overlay a top-level field (repeatable). The value is
  parsed as JSON when possible (`1000` → int, `true` → bool), else kept as a string.

The CLI does not validate the body — the server owns the schema and returns a
`validation_failed` error envelope if it is wrong.

## Examples

```bash
# Export config once
export GATEWAY_ADMIN_URL="https://abc123.execute-api.us-east-1.amazonaws.com/prod"
export GATEWAY_CLIENT_ID="…"  GATEWAY_CLIENT_SECRET="…"
export COGNITO_DOMAIN="my-gateway"  AWS_REGION="us-east-1"

# 1) List teams
uv run admin-cli teams list

# 2) Register a team (inline --set fields)
uv run admin-cli teams create \
  --set team_name=payments-svc \
  --set contact_email=payments@example.com \
  --set tier=premium

# 3) Create a budget from a file, then page through budgets
uv run admin-cli budgets create --body @budget.json
uv run admin-cli budgets list --cursor "<next_cursor from a prior list>"

# 4) Upsert a pricing override, raw JSON output
uv run admin-cli --json pricing upsert bedrock claude-sonnet-4 \
  --set input_per_1k=0.003 --set output_per_1k=0.015
```

On an error, the CLI prints `error: <code>: <message>` to stderr and exits
non-zero (no stack trace).

## Development

```bash
cd clients/admin_cli
uv sync
uv run pytest -q          # all mocked — no live network / AWS
uv run ruff check .
uv run ruff format --check .
```

Tests use stdlib `httpx.MockTransport` (no `respx` dependency) to mock the
Cognito token endpoint and the admin API, covering token acquisition, list
envelope parsing, the 401→refresh→retry path, and error-envelope handling.
