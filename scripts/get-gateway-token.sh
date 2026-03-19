#!/usr/bin/env bash
#
# get-gateway-token.sh — Obtain a Cognito M2M access token for the AI Gateway.
#
# Reads GATEWAY_CLIENT_ID, GATEWAY_CLIENT_SECRET, and GATEWAY_TOKEN_ENDPOINT
# from the environment. Outputs the raw access_token on stdout (no trailing
# newline) for use as a Bearer token or apiKeyHelper.
#
# Exit codes:
#   0  success
#   1  missing environment variable
#   2  token request failed (curl error or non-200 HTTP status)
#   3  JSON parsing failed or access_token missing in response

set -euo pipefail

# ---------------------------------------------------------------------------
# Validate required environment variables
# ---------------------------------------------------------------------------
missing=()
[[ -z "${GATEWAY_CLIENT_ID:-}" ]]       && missing+=("GATEWAY_CLIENT_ID")
[[ -z "${GATEWAY_CLIENT_SECRET:-}" ]]   && missing+=("GATEWAY_CLIENT_SECRET")
[[ -z "${GATEWAY_TOKEN_ENDPOINT:-}" ]]  && missing+=("GATEWAY_TOKEN_ENDPOINT")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "error: missing required environment variable(s): ${missing[*]}" >&2
  echo "hint:  export GATEWAY_CLIENT_ID, GATEWAY_CLIENT_SECRET, and GATEWAY_TOKEN_ENDPOINT" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Request token from Cognito token endpoint (client_credentials grant)
# ---------------------------------------------------------------------------
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
  --write-out "%{http_code}") || {
    echo "error: curl request to token endpoint failed" >&2
    exit 2
  }

if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
  echo "error: token endpoint returned HTTP ${http_code}" >&2
  [[ -s "$response_body" ]] && cat "$response_body" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Extract access_token from JSON response using python3
# ---------------------------------------------------------------------------
python3 -c "
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    token = data['access_token']
    if not token:
        raise ValueError('access_token is empty')
    print(token, end='')
except (KeyError, ValueError, json.JSONDecodeError) as e:
    print(f'error: failed to extract access_token: {e}', file=sys.stderr)
    sys.exit(1)
" "$response_body" || exit 3
