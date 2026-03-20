# Troubleshooting

Solutions for common issues when using the AI Gateway.

---

## 401 Unauthorized

The ALB rejected the request because the JWT is invalid or missing.

**Possible causes:**

- **Expired token** -- Cognito JWTs have a 1-hour TTL. Re-run the token script:
  ```bash
  TOKEN=$(./scripts/get-gateway-token.sh)
  ```
- **Missing `apiKeyHelper`** (Claude Code) -- Ensure the helper is set and the script is executable:
  ```bash
  claude config set --global apiKeyHelper ~/workplace/ai-gateway/scripts/get-gateway-token.sh
  chmod +x ~/workplace/ai-gateway/scripts/get-gateway-token.sh
  ```
- **Invalid JWT** -- Verify the token is well-formed:
  ```bash
  echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool
  ```
- **TTL not set as env var** (Claude Code) -- `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` must be a real environment variable, not set in the settings.json `env` block (bug #7660).

---

## 403 Forbidden

The request was authenticated but rejected by authorization or WAF rules.

**Possible causes:**

- **Wrong scope** -- The Cognito app client may not have the required `https://gateway.internal/invoke` scope. Contact the gateway admin to verify client configuration.
- **WAF block** -- AWS WAF may have blocked the request. Check the `x-amzn-waf-action` response header.
- **IP rate limit exceeded** -- WAF enforces a 2,000 requests/5-min per-IP limit. Wait and retry, or contact the admin for a higher threshold.

---

## Connection Refused / Timeout

The gateway URL is unreachable.

**Possible causes:**

- **Wrong gateway URL** -- Verify the URL is correct:
  ```bash
  curl -s -o /dev/null -w "%{http_code}" ${GATEWAY_URL}/
  ```
  Expect `200`. If you get no response, the URL is wrong or the gateway is down.
- **VPN required** -- If the ALB is in a private network, confirm your VPN is connected.
- **Unhealthy ALB target** -- The ALB health check path is `/` on port 8787. If all targets are unhealthy, the ALB returns `503`. Check ECS service events in the AWS console.

---

## Missing `x-portkey-provider` Header

```
{"error": "provider is not set"}
```

Every request must include the `x-portkey-provider` header. Verify per agent:

=== "Claude Code"

    Check that `ANTHROPIC_CUSTOM_HEADERS` is set (newline-separated format):
    ```bash
    echo "$ANTHROPIC_CUSTOM_HEADERS"
    # Should output: x-portkey-provider: anthropic
    ```

=== "OpenCode"

    Check that `options.headers` is present in `opencode.json`:
    ```json
    "options": {
      "headers": {
        "x-portkey-provider": "openai"
      }
    }
    ```

=== "Goose"

    Check that `OPENAI_EXTRA_HEADERS` is set:
    ```bash
    echo "$OPENAI_EXTRA_HEADERS"
    # Should output: x-portkey-provider: openai
    ```

=== "Continue.dev"

    Check that `requestOptions.headers` is present in `~/.continue/config.yaml`:
    ```yaml
    requestOptions:
      headers:
        x-portkey-provider: openai
    ```

=== "LangChain"

    Check that `default_headers` is passed to `ChatOpenAI`:
    ```python
    llm = ChatOpenAI(
        default_headers={"x-portkey-provider": "openai"},
        ...
    )
    ```

=== "Codex CLI"

    Check that the headers section exists in `~/.codex/config.toml`:
    ```toml
    [model_providers.gateway.headers]
    x-portkey-provider = "openai"
    ```

---

## Invalid Grant / Token Endpoint Error

```
{"error": "invalid_grant"}
```

The Cognito token request failed.

**Possible causes:**

- **Wrong token endpoint** -- Verify `GATEWAY_TOKEN_ENDPOINT` is the full URL:
  ```
  https://<domain>.auth.<region>.amazoncognito.com/oauth2/token
  ```
- **Wrong credentials** -- Confirm `GATEWAY_CLIENT_ID` and `GATEWAY_CLIENT_SECRET` are correct.
- **Wrong grant type** -- The Cognito app client must be configured for `client_credentials` grant type. Contact the gateway admin if this is not set.

---

## MCP Tool Search Disabled (Claude Code)

When connected to a non-first-party host, Claude Code disables MCP tool search by default. Symptoms: tool search returns no results, or tools from MCP servers are not discovered.

**Fix:**

```bash
export ENABLE_TOOL_SEARCH=true
```

Add this to the same shell profile block as your other gateway environment variables.

---

## Provider Override Not Working

Requests are going to `api.openai.com` instead of the gateway.

=== "Codex CLI"

    Do **not** use the built-in `openai` provider name. Codex CLI does not allow overriding it. Use a custom provider name:
    ```toml
    [model_providers.gateway]
    name = "AI Gateway"
    base_url = "${GATEWAY_URL}/v1"
    ```

    Launch with: `codex --provider gateway --model gpt-4.1`

=== "Goose"

    Use `OPENAI_HOST` (not `OPENAI_BASE_URL`), and **omit** the `/v1` suffix:
    ```bash
    export OPENAI_HOST="${GATEWAY_URL}"
    ```

=== "Claude Code"

    Ensure `ANTHROPIC_BASE_URL` is exported in your shell profile, not only set in the settings JSON.

---

## Config Precedence

When environment variables and config files conflict, the following precedence applies:

| Agent | Precedence (highest first) |
|---|---|
| **Claude Code** | Env vars > `claude config` settings > defaults |
| **OpenCode** | `opencode.json` in project root > global config |
| **Goose** | Env vars > `~/.config/goose/config.yaml` |
| **Continue.dev** | `~/.continue/config.yaml` (only source) |
| **LangChain** | Constructor args > env vars |
| **Codex CLI** | CLI flags > `config.toml` > env vars |

!!! tip "When in doubt"
    If a setting is not taking effect, check whether a higher-precedence source is overriding it. For example, a `OPENAI_HOST` env var will override a `config.yaml` setting in Goose, but a `--provider` CLI flag will override `config.toml` in Codex CLI.
