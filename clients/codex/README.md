# Codex client — AI Gateway enterprise config

Managed configuration that points the OpenAI **Codex** client at the AI Gateway,
so Codex's Responses traffic reaches GPT-5.5 / GPT-5.4 (and gpt-oss) on Amazon
Bedrock **through the gateway** — inside the customer AWS boundary, with the
gateway's hooks, logging, and cost attribution applied.

## How it works

Codex is Responses-API-only. The flagship GPT-5.5/5.4 models on Bedrock are also
Responses-only, served at the OpenAI-compatible **mantle** endpoint
(`https://bedrock-mantle.<region>.api.aws/openai/v1/responses`). The gateway
proxies to mantle with Portkey's stock `openai` provider + a pinned
`custom_host` — **no fork, no Converse translation**. The gateway holds the
Bedrock API key server-side; developer endpoints present only a gateway token.

```
Codex ──Responses──▶ AI Gateway ──openai provider + custom_host──▶ bedrock-mantle/openai/v1/responses
                       (hooks, logging, cost attribution, isolation)
```

> Path note: the flagship GPT-5.5/5.4 live on the mantle `/openai/v1` base;
> gpt-oss-120b/-20b live on the `/v1` base (Chat Completions, served by the
> gateway's `bedrock` provider). The gateway config sets `custom_host` per
> model family — verified live, see the project plan.

## Files

| File | Path on endpoint | Purpose |
|---|---|---|
| `config.toml` | `~/.codex/config.toml` | Points Codex at the gateway; `wire_api = "responses"`. |
| `requirements.toml` | Codex managed-config path | Admin-enforced pins (provider can't be repointed) + client-side MCP allow/deny. |

## Deploy

Push both files via the same MDM channel (Jamf / Intune / GPO) that delivers the
Claude Code managed settings — no new MDM product. `requirements.toml` is
admin-enforced and machine-local-unoverridable, so a project `config.toml`
cannot repoint Codex at `api.openai.com`. Add a deployed-hash audit step to
confirm the on-endpoint files match this repo source of truth.

## Constraints

- The gateway is **not** an MCP endpoint or funnel. MCP allow/deny is enforced
  client-side in `requirements.toml`, decoupled from inference routing.
- Replace `<gateway-host>` with the in-VPC ALB hostname; set the fleet region.
- `AI_GATEWAY_TOKEN` is the per-developer gateway bearer (Cognito JWT / gateway
  token), **not** an AWS or OpenAI key — those never touch the endpoint.
