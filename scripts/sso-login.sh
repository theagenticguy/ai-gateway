#!/usr/bin/env bash
#
# sso-login.sh — Interactive SSO login via Cognito Hosted UI.
#
# Opens the Cognito Hosted UI in the default browser, starts a local HTTP
# server on port 3000 to capture the OAuth callback, exchanges the
# authorization code for tokens, and prints the access_token to stdout.
#
# Required environment variables:
#   GATEWAY_USER_CLIENT_ID   — Cognito User SSO client ID
#   GATEWAY_HOSTED_UI_URL    — Full Cognito Hosted UI login URL
#   GATEWAY_TOKEN_ENDPOINT   — Cognito token endpoint (/oauth2/token)
#
# Exit codes:
#   0  success
#   1  missing environment variable
#   2  callback capture failed
#   3  token exchange failed

set -euo pipefail

CALLBACK_PORT="${CALLBACK_PORT:-3000}"
CALLBACK_URI="http://localhost:${CALLBACK_PORT}/callback"

# ---------------------------------------------------------------------------
# Validate required environment variables
# ---------------------------------------------------------------------------
missing=()
[[ -z "${GATEWAY_USER_CLIENT_ID:-}" ]]  && missing+=("GATEWAY_USER_CLIENT_ID")
[[ -z "${GATEWAY_HOSTED_UI_URL:-}" ]]   && missing+=("GATEWAY_HOSTED_UI_URL")
[[ -z "${GATEWAY_TOKEN_ENDPOINT:-}" ]]  && missing+=("GATEWAY_TOKEN_ENDPOINT")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "error: missing required environment variable(s): ${missing[*]}" >&2
  echo "hint:  export GATEWAY_USER_CLIENT_ID, GATEWAY_HOSTED_UI_URL, and GATEWAY_TOKEN_ENDPOINT" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Open browser to Hosted UI
# ---------------------------------------------------------------------------
echo "Opening Cognito Hosted UI in browser..." >&2

if command -v xdg-open &>/dev/null; then
  xdg-open "${GATEWAY_HOSTED_UI_URL}" 2>/dev/null &
elif command -v open &>/dev/null; then
  open "${GATEWAY_HOSTED_UI_URL}" &
else
  echo "Please open this URL in your browser:" >&2
  echo "  ${GATEWAY_HOSTED_UI_URL}" >&2
fi

# ---------------------------------------------------------------------------
# Start local HTTP server to capture the OAuth callback
# ---------------------------------------------------------------------------
echo "Waiting for OAuth callback on http://localhost:${CALLBACK_PORT}/callback ..." >&2

auth_code=$(python3 -c "
import http.server
import urllib.parse
import sys
import threading

code_holder = [None]
server_holder = [None]

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == '/callback' and 'code' in params:
            code_holder[0] = params['code'][0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><h2>Login successful!</h2><p>You can close this tab.</p></body></html>')
            # Shut down the server after responding
            threading.Thread(target=server_holder[0].shutdown).start()
        elif parsed.path == '/callback' and 'error' in params:
            error = params.get('error_description', params.get('error', ['unknown']))[0]
            self.send_response(400)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(f'<html><body><h2>Login failed</h2><p>{error}</p></body></html>'.encode())
            threading.Thread(target=server_holder[0].shutdown).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress request logs

server = http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), CallbackHandler)
server_holder[0] = server
server.handle_request()
server.handle_request()  # handle the shutdown request too

if code_holder[0]:
    print(code_holder[0], end='')
else:
    sys.exit(1)
" "${CALLBACK_PORT}") || {
  echo "error: failed to capture authorization code from callback" >&2
  exit 2
}

if [[ -z "${auth_code}" ]]; then
  echo "error: no authorization code received" >&2
  exit 2
fi

echo "Authorization code received, exchanging for tokens..." >&2

# ---------------------------------------------------------------------------
# Exchange authorization code for tokens
# ---------------------------------------------------------------------------
response_body=$(mktemp)
trap 'rm -f "$response_body"' EXIT

http_code=$(curl --silent --show-error \
  --request POST \
  --url "${GATEWAY_TOKEN_ENDPOINT}" \
  --header "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "client_id=${GATEWAY_USER_CLIENT_ID}" \
  --data-urlencode "code=${auth_code}" \
  --data-urlencode "redirect_uri=${CALLBACK_URI}" \
  --output "$response_body" \
  --write-out "%{http_code}") || {
    echo "error: curl request to token endpoint failed" >&2
    exit 3
  }

if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
  echo "error: token endpoint returned HTTP ${http_code}" >&2
  [[ -s "$response_body" ]] && cat "$response_body" >&2
  exit 3
fi

# ---------------------------------------------------------------------------
# Extract access_token from JSON response
# ---------------------------------------------------------------------------
python3 -c "
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    token = data['access_token']
    if not token:
        raise ValueError('access_token is empty')
    # Also show id_token info on stderr for debugging
    if 'id_token' in data:
        print(f'ID token present (use for user identity)', file=sys.stderr)
    if 'refresh_token' in data:
        print(f'Refresh token present (valid for 30 days)', file=sys.stderr)
    print(token, end='')
except (KeyError, ValueError, json.JSONDecodeError) as e:
    print(f'error: failed to extract access_token: {e}', file=sys.stderr)
    sys.exit(1)
" "$response_body" || exit 3
