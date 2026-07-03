#!/usr/bin/env bash
#
# check-health.sh — Health check for the AI Gateway.
#
# Tests gateway connectivity, authentication, token validity,
# and optionally a live inference probe (agentgateway routes server-side).
#
# Usage:
#   ./check-health.sh [--url <gateway-url>] [--token <jwt>] [--providers]
#
# Environment variables (used as defaults):
#   GATEWAY_URL   — Gateway base URL
#   TOKEN         — JWT access token (or pipe from get-gateway-token.sh)
#
# Dependencies: curl, jq, base64, python3
#
# Exit codes:
#   0  all checks passed
#   1  one or more checks failed

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

PASS="${GREEN}PASS${NC}"
FAIL="${RED}FAIL${NC}"
WARN="${YELLOW}WARN${NC}"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
GATEWAY_URL="${GATEWAY_URL:-}"
TOKEN="${TOKEN:-}"
CHECK_PROVIDERS=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)       GATEWAY_URL="$2"; shift 2 ;;
    --token)     TOKEN="$2"; shift 2 ;;
    --providers) CHECK_PROVIDERS=true; shift ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --url <url>     Gateway base URL (or set GATEWAY_URL env var)
  --token <jwt>   JWT access token (or set TOKEN env var)
  --providers     Run a live inference probe against the gateway
  -h, --help      Show this help

Examples:
  $(basename "$0") --url https://gateway.example.com
  TOKEN=\$(./scripts/get-gateway-token.sh) $(basename "$0")
  $(basename "$0") --url https://gateway.example.com --token eyJ... --providers
EOF
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Prerequisite check
# ---------------------------------------------------------------------------
for cmd in curl jq python3 base64; do
  if ! command -v "$cmd" &>/dev/null; then
    printf "${RED}error:${NC} required command '%s' not found\n" "$cmd" >&2
    exit 1
  fi
done

if [[ -z "$GATEWAY_URL" ]]; then
  printf "${RED}error:${NC} Gateway URL is required. Use --url or set GATEWAY_URL.\n" >&2
  exit 1
fi

# Strip trailing slash
GATEWAY_URL="${GATEWAY_URL%/}"

overall_status=0

printf "\n${BOLD}${CYAN}AI Gateway Health Check${NC}\n"
printf "${CYAN}%-40s${NC}\n" "$(printf '%.0s-' {1..40})"
printf "  Gateway: %s\n" "$GATEWAY_URL"
printf "  Time:    %s\n\n" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ---------------------------------------------------------------------------
# Check 1: Gateway connectivity
# ---------------------------------------------------------------------------
printf "%-36s " "Gateway connectivity (GET /) ..."
response_body=$(mktemp)
trap 'rm -f "$response_body"' EXIT

http_code=$(curl --silent --show-error --max-time 10 \
  -o "$response_body" -w "%{http_code}" \
  "${GATEWAY_URL}/" 2>/dev/null) || http_code="000"

if [[ "$http_code" -eq 200 ]]; then
  printf "[${PASS}]  HTTP %s\n" "$http_code"
elif [[ "$http_code" == "000" ]]; then
  printf "[${FAIL}]  Connection failed\n"
  overall_status=1
else
  printf "[${WARN}]  HTTP %s\n" "$http_code"
fi

# ---------------------------------------------------------------------------
# Check 2: Auth check (if token provided)
# ---------------------------------------------------------------------------
if [[ -n "$TOKEN" ]]; then
  printf "%-36s " "Authentication (Bearer token) ..."
  auth_code=$(curl --silent --show-error --max-time 10 \
    -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${TOKEN}" \
    "${GATEWAY_URL}/" 2>/dev/null) || auth_code="000"

  if [[ "$auth_code" -eq 200 ]]; then
    printf "[${PASS}]  HTTP %s\n" "$auth_code"
  elif [[ "$auth_code" -eq 401 ]]; then
    printf "[${FAIL}]  HTTP 401 — token rejected\n"
    overall_status=1
  elif [[ "$auth_code" -eq 403 ]]; then
    printf "[${FAIL}]  HTTP 403 — forbidden\n"
    overall_status=1
  else
    printf "[${WARN}]  HTTP %s\n" "$auth_code"
  fi

  # -------------------------------------------------------------------
  # Token details
  # -------------------------------------------------------------------
  printf "\n${BOLD}Token Details${NC}\n"
  printf "${CYAN}%-40s${NC}\n" "$(printf '%.0s-' {1..40})"

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
    print(json.dumps(data))
except Exception:
    print('{}')
" 2>/dev/null) || claims="{}"

  # Token expiry
  exp=$(echo "$claims" | jq -r '.exp // empty' 2>/dev/null)
  if [[ -n "$exp" ]]; then
    now=$(date +%s)
    remaining=$(( exp - now ))
    exp_formatted=$(date -d "@${exp}" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || \
                    date -r "$exp" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || \
                    echo "epoch: $exp")
    printf "  %-24s " "Expires:"
    if [[ "$remaining" -gt 0 ]]; then
      minutes=$(( remaining / 60 ))
      printf "[${PASS}]  in %d min (%s)\n" "$minutes" "$exp_formatted"
    else
      printf "[${FAIL}]  EXPIRED %d sec ago (%s)\n" "$(( -remaining ))" "$exp_formatted"
      overall_status=1
    fi
  else
    printf "  %-24s [${WARN}]  no exp claim found\n" "Expires:"
  fi

  # Issued at
  iat=$(echo "$claims" | jq -r '.iat // empty' 2>/dev/null)
  if [[ -n "$iat" ]]; then
    iat_formatted=$(date -d "@${iat}" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || \
                    date -r "$iat" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || \
                    echo "epoch: $iat")
    printf "  %-24s %s\n" "Issued at:" "$iat_formatted"
  fi

  # Issuer
  iss=$(echo "$claims" | jq -r '.iss // empty' 2>/dev/null)
  if [[ -n "$iss" ]]; then
    printf "  %-24s %s\n" "Issuer:" "$iss"
  fi

  # Team / Client
  team=$(echo "$claims" | jq -r '.["custom:team"] // .team // empty' 2>/dev/null)
  if [[ -n "$team" ]]; then
    printf "  %-24s %s\n" "Team:" "$team"
  fi

  client_id=$(echo "$claims" | jq -r '.client_id // .sub // empty' 2>/dev/null)
  if [[ -n "$client_id" ]]; then
    printf "  %-24s %s\n" "Client ID:" "$client_id"
  fi

  # Scope
  scope=$(echo "$claims" | jq -r '.scope // empty' 2>/dev/null)
  if [[ -n "$scope" ]]; then
    printf "  %-24s %s\n" "Scope:" "$scope"
  fi

  # Token type
  token_use=$(echo "$claims" | jq -r '.token_use // empty' 2>/dev/null)
  if [[ -n "$token_use" ]]; then
    printf "  %-24s %s\n" "Token use:" "$token_use"
  fi

else
  printf "%-36s [${WARN}]  No token provided (skipping auth check)\n" "Authentication ..."
  printf "  Hint: TOKEN=\$(./scripts/get-gateway-token.sh) %s\n" "$(basename "$0")"
fi

# ---------------------------------------------------------------------------
# Check 3: Provider checks (optional)
# ---------------------------------------------------------------------------
if [[ "$CHECK_PROVIDERS" == "true" ]]; then
  printf "\n${BOLD}Inference Probe${NC}\n"
  printf "${CYAN}%-40s${NC}\n" "$(printf '%.0s-' {1..40})"

  if [[ -z "$TOKEN" ]]; then
    printf "  [${WARN}]  Skipping inference probe — no token provided\n"
  else
    # agentgateway routes server-side by path + model alias — there is no
    # per-provider dimension and no provider-selection header. A single probe
    # against /v1/chat/completions exercises the live provider failover chain.
    printf "  %-30s " "inference (/v1/chat/completions) ..."

    probe_body=$(mktemp)
    probe_code=$(curl --silent --show-error --max-time 15 \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{"model":"gpt-4.1","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}' \
      -o "$probe_body" \
      -w "%{http_code}" \
      "${GATEWAY_URL}/v1/chat/completions" 2>/dev/null) || probe_code="000"

    if [[ "$probe_code" -ge 200 && "$probe_code" -lt 300 ]]; then
      printf "[${PASS}]  HTTP %s\n" "$probe_code"
    elif [[ "$probe_code" -eq 401 ]]; then
      printf "[${FAIL}]  HTTP 401 — auth failed\n"
      overall_status=1
    elif [[ "$probe_code" -eq 422 || "$probe_code" -eq 400 ]]; then
      # 422/400 often means the gateway is reachable but model/params wrong
      printf "[${WARN}]  HTTP %s — gateway reachable but request error\n" "$probe_code"
    elif [[ "$probe_code" -eq 502 ]]; then
      printf "[${FAIL}]  HTTP 502 — upstream provider unreachable\n"
      overall_status=1
    elif [[ "$probe_code" -eq 503 ]]; then
      printf "[${FAIL}]  HTTP 503 — gateway overloaded\n"
      overall_status=1
    elif [[ "$probe_code" == "000" ]]; then
      printf "[${FAIL}]  Connection failed\n"
      overall_status=1
    else
      printf "[${WARN}]  HTTP %s\n" "$probe_code"
    fi

    rm -f "$probe_body"
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf "\n${CYAN}%-40s${NC}\n" "$(printf '%.0s-' {1..40})"
if [[ "$overall_status" -eq 0 ]]; then
  printf "${GREEN}${BOLD}All checks passed.${NC}\n\n"
else
  printf "${RED}${BOLD}One or more checks failed.${NC}\n"
  printf "Run with -h for usage, or see docs/user-guide/troubleshooting.md\n\n"
fi

exit "$overall_status"
