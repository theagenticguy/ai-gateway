#!/usr/bin/env bash
#
# sso-login.sh — SSO browser login for AI Gateway via Cognito.
#
# Starts a temporary HTTP server, opens the browser to the Cognito
# authorization URL, captures the authorization code from the callback,
# exchanges it for tokens, and prints the access_token to stdout.
#
# Environment variables (or pass via CLI args):
#   COGNITO_DOMAIN  — e.g. https://mypool.auth.us-east-1.amazoncognito.com
#   CLIENT_ID       — Cognito app-client ID (public client, no secret)
#   REDIRECT_URI    — Callback URL (default: http://localhost:3000/callback)
#
# Dependencies: python3, curl, jq
#
# Exit codes:
#   0  success (access_token printed to stdout)
#   1  missing prerequisite or argument
#   2  auth flow failed

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments / env
# ---------------------------------------------------------------------------
COGNITO_DOMAIN="${COGNITO_DOMAIN:-${1:-}}"
CLIENT_ID="${CLIENT_ID:-${2:-}}"
REDIRECT_URI="${REDIRECT_URI:-${3:-http://localhost:3000/callback}}"

if [[ -z "$COGNITO_DOMAIN" ]]; then
  echo "error: COGNITO_DOMAIN is required (env var or first argument)" >&2
  echo "usage: $0 <cognito-domain> <client-id> [redirect-uri]" >&2
  exit 1
fi

if [[ -z "$CLIENT_ID" ]]; then
  echo "error: CLIENT_ID is required (env var or second argument)" >&2
  exit 1
fi

# Strip trailing slash from domain
COGNITO_DOMAIN="${COGNITO_DOMAIN%/}"

# Extract port from redirect URI
LISTEN_PORT=$(python3 -c "
from urllib.parse import urlparse
print(urlparse('${REDIRECT_URI}').port or 3000)
")
CALLBACK_PATH=$(python3 -c "
from urllib.parse import urlparse
print(urlparse('${REDIRECT_URI}').path or '/callback')
")

# Check prerequisites
for cmd in python3 curl jq; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "error: required command '$cmd' not found" >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Temporary files and cleanup
# ---------------------------------------------------------------------------
AUTH_CODE_FILE=$(mktemp)
SERVER_PID_FILE=$(mktemp)
trap 'cleanup' EXIT INT TERM

cleanup() {
  if [[ -f "$SERVER_PID_FILE" ]]; then
    local pid
    pid=$(cat "$SERVER_PID_FILE" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$AUTH_CODE_FILE" "$SERVER_PID_FILE"
}

# ---------------------------------------------------------------------------
# Start temporary HTTP server to capture the callback
# ---------------------------------------------------------------------------
python3 - "$LISTEN_PORT" "$CALLBACK_PATH" "$AUTH_CODE_FILE" <<'PYSERVER' &
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

listen_port = int(sys.argv[1])
callback_path = sys.argv[2]
code_file = sys.argv[3]

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == callback_path:
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            error = params.get("error", [None])[0]

            if code:
                with open(code_file, "w") as f:
                    f.write(code)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"""<html><body style="font-family:system-ui;text-align:center;padding:60px">
                    <h1>Login Successful</h1>
                    <p>You can close this tab and return to your terminal.</p>
                    </body></html>""")
            elif error:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                error_desc = params.get("error_description", ["Unknown error"])[0]
                msg = f"<html><body style='font-family:system-ui;text-align:center;padding:60px'><h1>Login Failed</h1><p>{error}: {error_desc}</p></body></html>"
                self.wfile.write(msg.encode())
            else:
                self.send_response(400)
                self.end_headers()

            # Shutdown after handling the callback
            import threading
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress log output to avoid polluting stdout
        pass

server = HTTPServer(("127.0.0.1", listen_port), CallbackHandler)
print(f"Listening on http://127.0.0.1:{listen_port}{callback_path}", file=sys.stderr)
server.serve_forever()
PYSERVER

SERVER_PID=$!
echo "$SERVER_PID" > "$SERVER_PID_FILE"

# Give the server a moment to start
sleep 1

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "error: failed to start callback server on port ${LISTEN_PORT}" >&2
  echo "hint:  is another process using port ${LISTEN_PORT}?" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Build authorization URL and open browser
# ---------------------------------------------------------------------------
AUTHORIZE_URL="${COGNITO_DOMAIN}/oauth2/authorize?response_type=code&client_id=${CLIENT_ID}&redirect_uri=${REDIRECT_URI}&scope=openid+profile"

echo "Opening browser for SSO login..." >&2
echo "If the browser does not open, visit this URL manually:" >&2
echo "" >&2
echo "  ${AUTHORIZE_URL}" >&2
echo "" >&2

# Cross-platform browser open
if command -v xdg-open &>/dev/null; then
  xdg-open "$AUTHORIZE_URL" 2>/dev/null || true
elif command -v open &>/dev/null; then
  open "$AUTHORIZE_URL" 2>/dev/null || true
else
  echo "warning: could not detect browser opener. Please open the URL above manually." >&2
fi

# ---------------------------------------------------------------------------
# Wait for the callback (server will shut down after receiving it)
# ---------------------------------------------------------------------------
echo "Waiting for SSO callback..." >&2
wait "$SERVER_PID" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Read the authorization code
# ---------------------------------------------------------------------------
if [[ ! -s "$AUTH_CODE_FILE" ]]; then
  echo "error: did not receive an authorization code from the callback" >&2
  exit 2
fi

AUTH_CODE=$(cat "$AUTH_CODE_FILE")

# ---------------------------------------------------------------------------
# Exchange authorization code for tokens
# ---------------------------------------------------------------------------
echo "Exchanging authorization code for tokens..." >&2

token_response=$(mktemp)
trap 'rm -f "$AUTH_CODE_FILE" "$SERVER_PID_FILE" "$token_response"' EXIT

http_code=$(curl --silent --show-error \
  --request POST \
  --url "${COGNITO_DOMAIN}/oauth2/token" \
  --header "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "client_id=${CLIENT_ID}" \
  --data-urlencode "code=${AUTH_CODE}" \
  --data-urlencode "redirect_uri=${REDIRECT_URI}" \
  --output "$token_response" \
  --write-out "%{http_code}" \
  --max-time 15) || {
  echo "error: token exchange request failed (curl error)" >&2
  exit 2
}

if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
  echo "error: token endpoint returned HTTP ${http_code}" >&2
  [[ -s "$token_response" ]] && cat "$token_response" >&2
  echo "" >&2
  exit 2
fi

ACCESS_TOKEN=$(jq -r '.access_token // empty' "$token_response" 2>/dev/null)
if [[ -z "$ACCESS_TOKEN" ]]; then
  echo "error: no access_token in token response" >&2
  [[ -s "$token_response" ]] && cat "$token_response" >&2
  echo "" >&2
  exit 2
fi

echo "SSO login successful." >&2

# Print only the access token to stdout
printf '%s' "$ACCESS_TOKEN"
