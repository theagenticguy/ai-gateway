#!/usr/bin/env bash
#
# gateway-setup.sh — Interactive onboarding wizard for AI Gateway.
#
# Walks through gateway connectivity, authentication, agent selection,
# and generates the environment variables needed to route AI agent
# requests through the gateway.
#
# Dependencies: curl, jq, base64, python3 (standard on macOS & Linux)
#
# Exit codes:
#   0  success
#   1  user cancelled or prerequisite missing
#   2  connectivity/auth failure

set -euo pipefail

# ---------------------------------------------------------------------------
# Colors (disabled if stdout is not a terminal)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  BLUE='\033[0;34m'
  CYAN='\033[0;36m'
  BOLD='\033[1m'
  NC='\033[0m'
else
  RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' NC=''
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()    { printf "${BLUE}[INFO]${NC}  %s\n" "$*"; }
success() { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }
header()  { printf "\n${BOLD}${CYAN}=== %s ===${NC}\n\n" "$*"; }

prompt_default() {
  local prompt="$1" default="$2" var_name="$3"
  printf "${BOLD}%s${NC}" "$prompt"
  if [[ -n "$default" ]]; then
    printf " [%s]" "$default"
  fi
  printf ": "
  read -r input
  eval "$var_name=\"${input:-$default}\""
}

prompt_choice() {
  local prompt="$1" var_name="$2"
  shift 2
  local options=("$@")
  printf "\n${BOLD}%s${NC}\n" "$prompt"
  local i=1
  for opt in "${options[@]}"; do
    printf "  ${CYAN}%d)${NC} %s\n" "$i" "$opt"
    ((i++))
  done
  printf "\nEnter choice [1-%d]: " "${#options[@]}"
  read -r choice
  if [[ -z "$choice" ]] || [[ "$choice" -lt 1 ]] || [[ "$choice" -gt "${#options[@]}" ]]; then
    error "Invalid choice"
    exit 1
  fi
  eval "$var_name=\"$choice\""
}

check_command() {
  if ! command -v "$1" &>/dev/null; then
    error "Required command '$1' not found. Please install it first."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Prerequisite check
# ---------------------------------------------------------------------------
header "AI Gateway Setup Wizard"
info "Checking prerequisites..."

for cmd in curl jq python3 base64; do
  check_command "$cmd"
done
success "All prerequisites found (curl, jq, python3, base64)"

# ---------------------------------------------------------------------------
# Step 1: Gateway URL & connectivity
# ---------------------------------------------------------------------------
header "Step 1: Gateway Connection"

default_url="${GATEWAY_URL:-}"
prompt_default "Gateway URL (e.g. https://gateway.example.com)" "$default_url" GATEWAY_URL

# Strip trailing slash
GATEWAY_URL="${GATEWAY_URL%/}"

if [[ -z "$GATEWAY_URL" ]]; then
  error "Gateway URL cannot be empty"
  exit 1
fi

info "Testing connectivity to ${GATEWAY_URL}/ ..."
http_code=$(curl --silent --show-error --max-time 10 \
  -o /dev/null -w "%{http_code}" "${GATEWAY_URL}/" 2>/dev/null) || {
  error "Cannot reach ${GATEWAY_URL}/ — check the URL and your network/VPN."
  exit 2
}

if [[ "$http_code" -eq 200 ]]; then
  success "Gateway is reachable (HTTP ${http_code})"
else
  warn "Gateway returned HTTP ${http_code} (expected 200). It may still work for authenticated requests."
fi

# ---------------------------------------------------------------------------
# Step 2: Auth method selection
# ---------------------------------------------------------------------------
header "Step 2: Authentication"

prompt_choice "Select authentication method:" AUTH_METHOD \
  "M2M credentials (client_id + client_secret)" \
  "SSO browser login" \
  "Existing JWT token"

TOKEN=""

case "$AUTH_METHOD" in
  1)
    # M2M credentials
    info "Enter your M2M credentials (from your team admin or terraform output)."
    prompt_default "Client ID" "${GATEWAY_CLIENT_ID:-}" GATEWAY_CLIENT_ID
    prompt_default "Client Secret" "${GATEWAY_CLIENT_SECRET:-}" GATEWAY_CLIENT_SECRET
    prompt_default "Token Endpoint" "${GATEWAY_TOKEN_ENDPOINT:-}" GATEWAY_TOKEN_ENDPOINT

    if [[ -z "$GATEWAY_CLIENT_ID" ]] || [[ -z "$GATEWAY_CLIENT_SECRET" ]] || [[ -z "$GATEWAY_TOKEN_ENDPOINT" ]]; then
      error "All three credential fields are required."
      exit 1
    fi

    info "Requesting token via client_credentials grant..."
    response_body=$(mktemp)
    trap 'rm -f "$response_body"' EXIT

    http_code=$(curl --silent --show-error \
      --request POST \
      --url "${GATEWAY_TOKEN_ENDPOINT}" \
      --header "Content-Type: application/x-www-form-urlencoded" \
      --data-urlencode "grant_type=client_credentials" \
      --data-urlencode "client_id=${GATEWAY_CLIENT_ID}" \
      --data-urlencode "client_secret=${GATEWAY_CLIENT_SECRET}" \
      --output "$response_body" \
      --write-out "%{http_code}" \
      --max-time 15) || {
      error "Token request failed (curl error)."
      exit 2
    }

    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
      error "Token endpoint returned HTTP ${http_code}"
      [[ -s "$response_body" ]] && cat "$response_body" >&2
      printf "\n" >&2
      exit 2
    fi

    TOKEN=$(jq -r '.access_token // empty' "$response_body" 2>/dev/null)
    if [[ -z "$TOKEN" ]]; then
      error "No access_token in response. Response body:"
      cat "$response_body" >&2
      printf "\n" >&2
      exit 2
    fi
    success "Token acquired via client_credentials grant"
    ;;

  2)
    # SSO browser login
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SSO_SCRIPT="${SCRIPT_DIR}/sso-login.sh"

    if [[ ! -x "$SSO_SCRIPT" ]]; then
      error "SSO login script not found or not executable at ${SSO_SCRIPT}"
      info "Run: chmod +x ${SSO_SCRIPT}"
      exit 1
    fi

    prompt_default "Cognito Domain (e.g. https://mypool.auth.us-east-1.amazoncognito.com)" "${COGNITO_DOMAIN:-}" COGNITO_DOMAIN
    prompt_default "Client ID (SSO app client)" "${SSO_CLIENT_ID:-}" SSO_CLIENT_ID
    prompt_default "Redirect URI" "${SSO_REDIRECT_URI:-http://localhost:3000/callback}" SSO_REDIRECT_URI

    info "Launching SSO browser login..."
    TOKEN=$(COGNITO_DOMAIN="$COGNITO_DOMAIN" CLIENT_ID="$SSO_CLIENT_ID" REDIRECT_URI="$SSO_REDIRECT_URI" \
      "$SSO_SCRIPT") || {
      error "SSO login failed."
      exit 2
    }
    success "Token acquired via SSO browser login"
    ;;

  3)
    # Existing JWT
    printf "${BOLD}Paste your JWT token:${NC} "
    read -r TOKEN
    if [[ -z "$TOKEN" ]]; then
      error "Token cannot be empty."
      exit 1
    fi
    success "Using provided JWT token"
    ;;
esac

# ---------------------------------------------------------------------------
# Step 3: Validate token
# ---------------------------------------------------------------------------
header "Step 3: Token Validation"

info "Validating token against gateway..."
validate_code=$(curl --silent --show-error --max-time 10 \
  -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${TOKEN}" \
  "${GATEWAY_URL}/" 2>/dev/null) || {
  warn "Could not reach gateway for token validation."
  validate_code="000"
}

if [[ "$validate_code" -eq 200 ]]; then
  success "Token is valid (HTTP 200)"
elif [[ "$validate_code" -eq 401 ]]; then
  error "Token was rejected (HTTP 401). It may be expired or invalid."
  exit 2
else
  warn "Gateway returned HTTP ${validate_code}. Proceeding anyway."
fi

# Decode token claims (without verification — informational only)
token_payload=$(echo "$TOKEN" | cut -d. -f2)
# Fix base64 padding for both macOS and Linux
padding_len=$(( 4 - ${#token_payload} % 4 ))
if [[ "$padding_len" -lt 4 ]]; then
  token_payload="${token_payload}$(printf '=%.0s' $(seq 1 "$padding_len"))"
fi

claims=$(echo "$token_payload" | base64 -d 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(json.dumps(data, indent=2))
except Exception:
    print('{}')
" 2>/dev/null) || claims="{}"

exp=$(echo "$claims" | jq -r '.exp // empty' 2>/dev/null)
if [[ -n "$exp" ]]; then
  now=$(date +%s)
  remaining=$(( exp - now ))
  if [[ "$remaining" -gt 0 ]]; then
    minutes=$(( remaining / 60 ))
    success "Token expires in ${minutes} minutes ($(date -d "@${exp}" 2>/dev/null || date -r "$exp" 2>/dev/null || echo "epoch: $exp"))"
  else
    warn "Token is already expired (expired $(( -remaining )) seconds ago)"
  fi
fi

team=$(echo "$claims" | jq -r '.["custom:team"] // .team // .client_id // empty' 2>/dev/null)
scope=$(echo "$claims" | jq -r '.scope // empty' 2>/dev/null)
if [[ -n "$team" ]]; then
  info "Team / Client: ${team}"
fi
if [[ -n "$scope" ]]; then
  info "Scope: ${scope}"
fi

# ---------------------------------------------------------------------------
# Step 4: Agent selection
# ---------------------------------------------------------------------------
header "Step 4: Agent Selection"

prompt_choice "Which AI agent will you use?" AGENT_CHOICE \
  "Claude Code (Anthropic API)" \
  "Claude Code via Bedrock" \
  "OpenCode" \
  "Goose (by Block)" \
  "Continue.dev" \
  "LangChain / OpenAI Python SDK" \
  "Other / Custom"

# ---------------------------------------------------------------------------
# Step 5: Generate environment variables
# ---------------------------------------------------------------------------
header "Step 5: Configuration"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_SCRIPT="${SCRIPT_DIR}/get-gateway-token.sh"

case "$AGENT_CHOICE" in
  1)
    # Claude Code (Anthropic)
    info "Generating Claude Code configuration..."
    printf "\n${BOLD}Run this command to set apiKeyHelper:${NC}\n\n"
    printf "  claude config set --global apiKeyHelper %s\n" "$TOKEN_SCRIPT"

    printf "\n${BOLD}Add these to your shell profile (~/.zshrc or ~/.bashrc):${NC}\n\n"
    cat <<ENVBLOCK
  # --- AI Gateway (Claude Code) ---
  # No provider header: agentgateway routes server-side by path + model alias.
  export GATEWAY_URL="${GATEWAY_URL}"
  export ANTHROPIC_BASE_URL="${GATEWAY_URL}"
  export CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000
  export ENABLE_TOOL_SEARCH=true
ENVBLOCK

    if [[ "$AUTH_METHOD" == "1" ]]; then
      cat <<ENVBLOCK
  export GATEWAY_CLIENT_ID="${GATEWAY_CLIENT_ID}"
  export GATEWAY_CLIENT_SECRET="${GATEWAY_CLIENT_SECRET}"
  export GATEWAY_TOKEN_ENDPOINT="${GATEWAY_TOKEN_ENDPOINT}"
ENVBLOCK
    fi
    printf "\n"
    ;;

  2)
    # Claude Code via Bedrock
    info "Generating Claude Code (Bedrock) configuration..."
    printf "\n${BOLD}Run this command to set apiKeyHelper:${NC}\n\n"
    printf "  claude config set --global apiKeyHelper %s\n" "$TOKEN_SCRIPT"

    printf "\n${BOLD}Add these to your shell profile (~/.zshrc or ~/.bashrc):${NC}\n\n"
    cat <<ENVBLOCK
  # --- AI Gateway (Claude Code via Bedrock) ---
  # No provider header: the gateway's priority-group failover chain (Bedrock
  # primary, Anthropic-direct fallback) selects the backend server-side.
  export GATEWAY_URL="${GATEWAY_URL}"
  export ANTHROPIC_BASE_URL="${GATEWAY_URL}"
  export CLAUDE_CODE_API_KEY_HELPER_TTL_MS=3000000
  export ENABLE_TOOL_SEARCH=true
ENVBLOCK

    if [[ "$AUTH_METHOD" == "1" ]]; then
      cat <<ENVBLOCK
  export GATEWAY_CLIENT_ID="${GATEWAY_CLIENT_ID}"
  export GATEWAY_CLIENT_SECRET="${GATEWAY_CLIENT_SECRET}"
  export GATEWAY_TOKEN_ENDPOINT="${GATEWAY_TOKEN_ENDPOINT}"
ENVBLOCK
    fi
    printf "\n"
    ;;

  3)
    # OpenCode
    info "Generating OpenCode configuration..."
    printf "\n${BOLD}Add these to your shell profile:${NC}\n\n"
    cat <<ENVBLOCK
  # --- AI Gateway (OpenCode) ---
  export GATEWAY_URL="${GATEWAY_URL}"
  export OPENAI_API_KEY="\$(${TOKEN_SCRIPT})"
ENVBLOCK

    if [[ "$AUTH_METHOD" == "1" ]]; then
      cat <<ENVBLOCK
  export GATEWAY_CLIENT_ID="${GATEWAY_CLIENT_ID}"
  export GATEWAY_CLIENT_SECRET="${GATEWAY_CLIENT_SECRET}"
  export GATEWAY_TOKEN_ENDPOINT="${GATEWAY_TOKEN_ENDPOINT}"
ENVBLOCK
    fi

    printf "\n${BOLD}Create/edit opencode.json in your project root:${NC}\n\n"
    cat <<'JSONBLOCK'
  {
    "$schema": "https://opencode.ai/config.json",
    "provider": {
      "gateway": {
        "id": "gateway",
        "name": "AI Gateway",
        "type": "@ai-sdk/openai-compatible",
        "options": {
JSONBLOCK
    printf '          "baseURL": "%s/v1"\n' "${GATEWAY_URL}"
    cat <<'JSONBLOCK'
        },
        "models": {
          "gpt-4.1": {
            "id": "gpt-4.1",
            "name": "GPT-4.1 (via Gateway)",
            "type": "chat",
            "attachment": true
          }
        }
      }
    },
    "model": {
      "chat": "gateway/gpt-4.1"
    }
  }
JSONBLOCK
    printf "\n"
    ;;

  4)
    # Goose
    info "Generating Goose configuration..."
    printf "\n${BOLD}Add these to your shell profile:${NC}\n\n"
    cat <<ENVBLOCK
  # --- AI Gateway (Goose) ---
  # No provider header: agentgateway selects the upstream provider server-side.
  export GATEWAY_URL="${GATEWAY_URL}"
  export GOOSE_PROVIDER=openai
  export OPENAI_HOST="${GATEWAY_URL}"
  export OPENAI_API_KEY="\$(${TOKEN_SCRIPT})"
ENVBLOCK

    if [[ "$AUTH_METHOD" == "1" ]]; then
      cat <<ENVBLOCK
  export GATEWAY_CLIENT_ID="${GATEWAY_CLIENT_ID}"
  export GATEWAY_CLIENT_SECRET="${GATEWAY_CLIENT_SECRET}"
  export GATEWAY_TOKEN_ENDPOINT="${GATEWAY_TOKEN_ENDPOINT}"
ENVBLOCK
    fi
    printf "\n"
    ;;

  5)
    # Continue.dev
    info "Generating Continue.dev configuration..."
    printf "\n${BOLD}Edit ~/.continue/config.yaml:${NC}\n\n"
    cat <<YAMLBLOCK
  # No requestOptions.headers: the gateway maps the requested model onto a
  # backend via modelAliases and routes through its priority-group failover chain.
  models:
    - name: GPT-4.1 (Gateway)
      provider: openai
      model: gpt-4.1
      apiBase: "${GATEWAY_URL}/v1"
      apiKey: "<token-from-get-gateway-token.sh>"

    - name: Claude Sonnet (Gateway)
      provider: openai
      model: claude-sonnet-4-20250514
      apiBase: "${GATEWAY_URL}/v1"
      apiKey: "<token-from-get-gateway-token.sh>"
YAMLBLOCK

    if [[ "$AUTH_METHOD" == "1" ]]; then
      printf "\n${BOLD}Add these to your shell profile:${NC}\n\n"
      cat <<ENVBLOCK
  export GATEWAY_CLIENT_ID="${GATEWAY_CLIENT_ID}"
  export GATEWAY_CLIENT_SECRET="${GATEWAY_CLIENT_SECRET}"
  export GATEWAY_TOKEN_ENDPOINT="${GATEWAY_TOKEN_ENDPOINT}"
ENVBLOCK
    fi
    printf "\n"
    warn "Continue reads config.yaml at startup. Refresh token and restart Continue when it expires."
    ;;

  6)
    # LangChain / OpenAI SDK
    info "Generating LangChain / OpenAI SDK configuration..."
    printf "\n${BOLD}Add these to your shell profile:${NC}\n\n"
    cat <<ENVBLOCK
  # --- AI Gateway (LangChain / OpenAI) ---
  export GATEWAY_URL="${GATEWAY_URL}"
  export OPENAI_API_KEY="\$(${TOKEN_SCRIPT})"
  export OPENAI_BASE_URL="${GATEWAY_URL}/v1"
ENVBLOCK

    if [[ "$AUTH_METHOD" == "1" ]]; then
      cat <<ENVBLOCK
  export GATEWAY_CLIENT_ID="${GATEWAY_CLIENT_ID}"
  export GATEWAY_CLIENT_SECRET="${GATEWAY_CLIENT_SECRET}"
  export GATEWAY_TOKEN_ENDPOINT="${GATEWAY_TOKEN_ENDPOINT}"
ENVBLOCK
    fi

    printf "\n${BOLD}Python example:${NC}\n\n"
    cat <<'PYBLOCK'
  from langchain_openai import ChatOpenAI

  # No custom headers: the gateway resolves the model against modelAliases and
  # its provider failover chain. Change `model` to target a different backend.
  llm = ChatOpenAI(
      model="gpt-4.1",
  )
  response = llm.invoke("Hello from LangChain via the AI Gateway")
  print(response.content)
PYBLOCK
    printf "\n"
    ;;

  7)
    # Other / Custom
    info "Generating generic configuration..."
    printf "\n${BOLD}Use these values to configure your agent:${NC}\n\n"
    printf "  Gateway URL:       %s\n" "${GATEWAY_URL}"
    printf "  OpenAI-compatible: %s/v1\n" "${GATEWAY_URL}"
    printf "  Auth header:       Authorization: Bearer <token>\n"
    printf "  Routing:           server-side — no provider header. Point your agent\n"
    printf "                     at the gateway URL with a valid JWT; the gateway\n"
    printf "                     picks the backend by model alias + request path\n"
    printf "                     (/v1/chat/completions or /v1/messages).\n"

    printf "\n${BOLD}Get a token:${NC}\n\n"
    printf "  TOKEN=\$(%s)\n" "${TOKEN_SCRIPT}"

    printf "\n${BOLD}Test with curl:${NC}\n\n"
    cat <<CURLBLOCK
  curl ${GATEWAY_URL}/v1/chat/completions \\
    -H "Authorization: Bearer \$TOKEN" \\
    -H "Content-Type: application/json" \\
    -d '{
      "model": "gpt-4.1",
      "messages": [{"role": "user", "content": "Hello"}]
    }'
CURLBLOCK

    if [[ "$AUTH_METHOD" == "1" ]]; then
      printf "\n${BOLD}Add these to your shell profile:${NC}\n\n"
      cat <<ENVBLOCK
  export GATEWAY_CLIENT_ID="${GATEWAY_CLIENT_ID}"
  export GATEWAY_CLIENT_SECRET="${GATEWAY_CLIENT_SECRET}"
  export GATEWAY_TOKEN_ENDPOINT="${GATEWAY_TOKEN_ENDPOINT}"
ENVBLOCK
    fi
    printf "\n"
    ;;
esac

# ---------------------------------------------------------------------------
# Step 6: Optional inference test
# ---------------------------------------------------------------------------
header "Step 6: Inference Test (Optional)"

printf "${BOLD}Run a test inference call?${NC} [y/N]: "
read -r run_test

if [[ "${run_test,,}" == "y" || "${run_test,,}" == "yes" ]]; then
  # Pick the API path based on agent choice. agentgateway routes server-side by
  # path + model alias, so there is no provider header to send.
  case "$AGENT_CHOICE" in
    1|2) api_path="/v1/messages" ;;
    *)   api_path="/v1/chat/completions" ;;
  esac

  info "Sending test prompt to ${GATEWAY_URL}${api_path} ..."

  test_body=$(mktemp)
  trap 'rm -f "$test_body"' EXIT

  if [[ "$api_path" == "/v1/messages" ]]; then
    # Anthropic Messages API format
    http_code=$(curl --silent --show-error --max-time 30 \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      -H "anthropic-version: 2023-06-01" \
      -d '{
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Say hello in exactly 5 words."}]
      }' \
      -o "$test_body" \
      -w "%{http_code}" \
      "${GATEWAY_URL}${api_path}") || {
      error "Test request failed (curl error)."
      http_code="000"
    }
  else
    # OpenAI Chat Completions format
    http_code=$(curl --silent --show-error --max-time 30 \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{
        "model": "gpt-4.1",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Say hello in exactly 5 words."}]
      }' \
      -o "$test_body" \
      -w "%{http_code}" \
      "${GATEWAY_URL}${api_path}") || {
      error "Test request failed (curl error)."
      http_code="000"
    }
  fi

  if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
    success "Inference test passed (HTTP ${http_code})"
    printf "\n${BOLD}Response:${NC}\n"
    jq '.' "$test_body" 2>/dev/null || cat "$test_body"
    printf "\n"

    # Show token usage if present
    usage=$(jq '.usage // empty' "$test_body" 2>/dev/null)
    if [[ -n "$usage" && "$usage" != "null" ]]; then
      input_tokens=$(echo "$usage" | jq '.input_tokens // .prompt_tokens // 0')
      output_tokens=$(echo "$usage" | jq '.output_tokens // .completion_tokens // 0')
      printf "\n${CYAN}Token usage:${NC} input=%s, output=%s\n" "$input_tokens" "$output_tokens"
    fi
  else
    error "Inference test returned HTTP ${http_code}"
    [[ -s "$test_body" ]] && jq '.' "$test_body" 2>/dev/null || cat "$test_body"
    printf "\n"
  fi
else
  info "Skipping inference test."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
header "Setup Complete"

success "AI Gateway is configured for your agent."
info "Gateway URL: ${GATEWAY_URL}"
info "Documentation: docs/agent-setup.md"
info "Health check:  scripts/check-health.sh"
info "Token script:  scripts/get-gateway-token.sh"
printf "\n"
