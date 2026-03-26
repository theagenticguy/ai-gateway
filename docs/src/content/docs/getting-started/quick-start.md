---
title: Quick Start
description: Interactive setup wizard for rapid deployment.
sidebar:
  order: 3
---
Get your AI agent connected to the gateway in under 5 minutes.

---

## Prerequisites

You need the following installed:

| Tool | Check | Purpose |
|------|-------|---------|
| `bash` | `bash --version` | Script runtime (4.0+ recommended) |
| `curl` | `curl --version` | HTTP requests |
| `jq` | `jq --version` | JSON parsing |
| `python3` | `python3 --version` | Token extraction and SSO server |
| `base64` | `echo test \| base64` | JWT decoding |

You also need one of the following for authentication:

- **M2M credentials**: `GATEWAY_CLIENT_ID`, `GATEWAY_CLIENT_SECRET`, and `GATEWAY_TOKEN_ENDPOINT` (from your team admin or `terraform output`)
- **SSO access**: A Cognito user pool configured for browser-based login
- **Existing JWT**: A valid access token obtained through another method

---

## Step 1: Run the Setup Wizard

The interactive wizard handles everything: connectivity testing, authentication, agent configuration, and an optional inference test.

```bash
./scripts/gateway-setup.sh
```

The wizard walks through six steps:

1. **Gateway connection** -- Enter the gateway URL, verify connectivity
2. **Authentication** -- Choose M2M credentials, SSO browser login, or paste an existing JWT
3. **Token validation** -- Confirm the token works against the gateway
4. **Agent selection** -- Pick your AI agent (Claude Code, OpenCode, Goose, Continue.dev, LangChain, or custom)
5. **Configuration** -- Get the exact environment variables and config files for your agent
6. **Inference test** -- Optionally send a test prompt to verify end-to-end

---

## Step 2: Apply the Configuration

Copy the environment variables the wizard outputs into your shell profile (`~/.zshrc` or `~/.bashrc`), then reload:

```bash
source ~/.zshrc
```

For agents that need config files (OpenCode, Continue.dev, Codex CLI), create or edit the files as shown by the wizard.

---

## Step 3: Verify

Run the health check to confirm everything is working:

```bash
# Basic connectivity check
./scripts/check-health.sh --url "$GATEWAY_URL"

# Full check with authentication
TOKEN=$(./scripts/get-gateway-token.sh)
TOKEN="$TOKEN" ./scripts/check-health.sh --url "$GATEWAY_URL" --token "$TOKEN"

# Include provider checks
TOKEN="$TOKEN" ./scripts/check-health.sh --url "$GATEWAY_URL" --token "$TOKEN" --providers
```

---

## What Each Script Does

| Script | Purpose |
|--------|---------|
| `scripts/gateway-setup.sh` | Interactive onboarding wizard |
| `scripts/get-gateway-token.sh` | Fetch a Cognito M2M access token (client_credentials grant) |
| `scripts/sso-login.sh` | SSO browser login flow (opens browser, captures callback) |
| `scripts/check-health.sh` | Health check: connectivity, auth, token details, provider status |

---

## Next Steps

- [Authentication](authentication.md) -- How the Cognito M2M auth flow works
- [Agent Setup](../user-guide/agent-setup.md) -- Detailed per-agent configuration
- [Troubleshooting](../user-guide/troubleshooting.md) -- Common errors and fixes