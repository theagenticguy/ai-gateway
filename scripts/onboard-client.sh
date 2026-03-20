#!/usr/bin/env bash
#
# onboard-client.sh -- Retrieve Cognito credentials for an onboarded team.
#
# Reads terraform output to extract the client ID, client secret, and token
# endpoint for a given team name.
#
# Usage:
#   ./onboard-client.sh <team-name>
#
# Prerequisites:
#   - Terraform state is accessible (run from infrastructure/ or use -chdir)
#   - The team must already exist in client_configs and have been applied
#
# Exit codes:
#   0  success
#   1  missing argument or team not found
#   2  terraform command failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="${SCRIPT_DIR}/../infrastructure"

if [[ $# -lt 1 ]]; then
  echo "usage: $(basename "$0") <team-name>" >&2
  echo "example: $(basename "$0") platform" >&2
  exit 1
fi

TEAM_NAME="$1"

echo "Fetching Terraform outputs for team '${TEAM_NAME}'..." >&2

client_ids_json=$(terraform -chdir="${INFRA_DIR}" output -json team_client_ids 2>/dev/null) || {
  echo "error: failed to read terraform output 'team_client_ids'" >&2
  echo "hint:  ensure you have run 'terraform apply' and have access to state" >&2
  exit 2
}

client_secrets_json=$(terraform -chdir="${INFRA_DIR}" output -json team_client_secrets 2>/dev/null) || {
  echo "error: failed to read terraform output 'team_client_secrets'" >&2
  exit 2
}

token_endpoint=$(terraform -chdir="${INFRA_DIR}" output -raw cognito_token_endpoint 2>/dev/null) || {
  echo "error: failed to read terraform output 'cognito_token_endpoint'" >&2
  exit 2
}

client_id=$(python3 -c "
import json, sys
data = json.loads(sys.argv[1])
team = sys.argv[2]
if team not in data:
    print(f\"error: team '{team}' not found in client_ids\", file=sys.stderr)
    print(f\"available teams: {', '.join(sorted(data.keys())) or '(none)'}\", file=sys.stderr)
    sys.exit(1)
print(data[team], end='')
" "${client_ids_json}" "${TEAM_NAME}") || exit 1

client_secret=$(python3 -c "
import json, sys
data = json.loads(sys.argv[1])
team = sys.argv[2]
if team not in data:
    print(f\"error: team '{team}' not found in client_secrets\", file=sys.stderr)
    sys.exit(1)
print(data[team], end='')
" "${client_secrets_json}" "${TEAM_NAME}") || exit 1

printf '\n=== AI Gateway Credentials: %s ===\n\n' "${TEAM_NAME}"
printf '  Client ID:      %s\n' "${client_id}"
printf '  Client Secret:  %s\n' "${client_secret}"
printf '  Token Endpoint: %s\n' "${token_endpoint}"
printf '\n--- Quick test ---\n\n'
printf '  export GATEWAY_CLIENT_ID="%s"\n' "${client_id}"
printf '  export GATEWAY_CLIENT_SECRET="%s"\n' "${client_secret}"
printf '  export GATEWAY_TOKEN_ENDPOINT="%s"\n' "${token_endpoint}"
printf '  ./scripts/get-gateway-token.sh\n\n'
