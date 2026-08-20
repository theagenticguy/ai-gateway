# AI Gateway

[![CI/CD Pipeline](https://github.com/theagenticguy/ai-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/theagenticguy/ai-gateway/actions/workflows/ci.yml)
[![CodeQL](https://github.com/theagenticguy/ai-gateway/actions/workflows/codeql.yml/badge.svg)](https://github.com/theagenticguy/ai-gateway/actions/workflows/codeql.yml)
[![codecov](https://codecov.io/gh/theagenticguy/ai-gateway/graph/badge.svg)](https://codecov.io/gh/theagenticguy/ai-gateway)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/theagenticguy/ai-gateway/badge)](https://scorecard.dev/viewer/?uri=github.com/theagenticguy/ai-gateway)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/12221/badge)](https://www.bestpractices.dev/en/projects/12221)
[![Release](https://img.shields.io/github/v/release/theagenticguy/ai-gateway)](https://github.com/theagenticguy/ai-gateway/releases)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-fe5196.svg)](https://www.conventionalcommits.org)

One endpoint for every AI coding agent in your organization, on your own AWS
account. The gateway routes OpenAI-format and Anthropic-format requests to
Bedrock, OpenAI, Anthropic, Google, and Azure OpenAI, authenticates teams with
short-lived Cognito tokens instead of shared provider keys, and meters every
request into per-team budgets, rate limits, and chargeback reports.

Claude Code, Codex CLI, OpenCode, Goose, Continue.dev, and LangChain all work
against it with nothing but a base URL and a token, because it serves both
`/v1/messages` (Anthropic Messages) and `/v1/chat/completions` (OpenAI Chat
Completions) natively.

**Documentation:** [user guide, developer guide, and reference](https://theagenticguy.github.io/ai-gateway/) ·
**Decisions:** [17 ADRs](adr/) · **Deep dive:** [engineering-docs/](engineering-docs/README.md)

## Quick start

Deploying the gateway (platform operator path):

```bash
git clone git@github.com:theagenticguy/ai-gateway.git
cd ai-gateway
mise install          # pinned toolchain: Python 3.13, Terraform, uv, every scanner
mise run install      # Python deps + git hooks

cd infrastructure
terraform init        # backend is s3; pass -backend-config for your state bucket
terraform plan -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars
```

Connecting an agent to a deployed gateway (user path):

```bash
./scripts/gateway-setup.sh    # interactive wizard: connectivity, auth, agent config
```

or by hand: fetch a token and point your agent at the ALB.

```bash
export GATEWAY_TOKEN=$(./scripts/get-gateway-token.sh)

# Claude Code
export ANTHROPIC_BASE_URL=https://<alb-dns>
export ANTHROPIC_AUTH_TOKEN=$GATEWAY_TOKEN

# OpenAI-format agents: base URL https://<alb-dns>/v1, key = the same token
export OPENAI_API_KEY=$GATEWAY_TOKEN
```

Per-agent instructions (all six agents, plus token caching) are in the
[agent setup guide](docs/src/content/docs/user-guide/agent-setup.md).

## Why it exists

Teams adopting coding agents hit the same three problems: provider API keys
get shared and never rotated, nobody can attribute spend to a team, and every
agent speaks a different API format. The usual answer is a hosted LLM proxy,
which puts an external vendor in the middle of every prompt.

This project is that proxy as first-party infrastructure: a Terraform stack
you deploy into your own account, with identity on Cognito, transport on an
ALB, and the data plane on [agentgateway](https://github.com/agentgateway/agentgateway),
a CNCF-sandbox Rust proxy. No request leaves your VPC except to the model
provider you routed it to.

We evaluated LiteLLM first and rejected it ([ADR-001](adr/001-portkey-oss-over-litellm.md)):
at evaluation time (March 2026) it carried 14 known CVEs including a critical
RCE, and a supply-chain compromise was disclosed days later. The original
Portkey OSS data plane was replaced by agentgateway in
[ADR-017](adr/017-agentgateway-data-plane-spike.md); LiteLLM was never a
dependency at any layer.

## How it works

Two planes, split in [ADR-014](adr/014-two-plane-architecture-split.md):

```text
agent ── Bearer JWT ──> ALB (TLS 1.3, WAF, native JWT validation)
                          │
                          ▼
                  ECS Fargate: agentgateway + OTel collector sidecar
                          │
        Bedrock / OpenAI / Anthropic / Google / Azure OpenAI

admin ── Cognito ──> API Gateway ──> 11 Lambda services (gwcore)
                                      teams · budgets · rate limits · routing
                                      pricing · usage · chargeback · audit
```

**Data plane.** The ALB validates the Cognito JWT natively (signature against
JWKS, `iss`, `exp`, `nbf`, `iat`, and the required scope), so a bad token is
rejected before anything downstream runs, at no per-request cost
([ADR-005](adr/005-alb-jwt-validation-over-api-gateway.md)). Valid requests
reach agentgateway on ECS Fargate, which serves both API formats, applies
routing and guardrail webhooks from rendered config, and emits traces,
metrics, and access logs through an OpenTelemetry collector sidecar. The VPC
is single-region, two-AZ, with one NAT gateway and VPC endpoints for ECR,
CloudWatch Logs, Secrets Manager, and S3
([ADR-003](adr/003-single-nat-gw-with-vpc-endpoints.md)). Provider keys live
in Secrets Manager and are injected into the task at runtime.

**Control plane.** Eleven Lambda services behind an API Gateway REST API with
a Cognito authorizer handle team registration, budget administration and
enforcement, rate limiting, routing config, pricing, usage, chargeback
reports, cost attribution, admin tokens, and pre-token claims. Eight are
deployed by the Terraform stack today; rate limiting, the usage API, and
pricing administration are implemented and tested in `src/` but not yet wired
to Lambda resources (each says so in its `__init__.py`). They share one
Python package, [`src/gwcore/`](src/gwcore/): one authentication path, one
response/error/pagination contract, in-process + ETag caching, an append-only
audit trail (Kinesis Firehose to Apache Iceberg on S3 Tables), and uniform
EMF metrics and structured logging
([ADR-016](adr/016-control-plane-api-foundation.md)).

**Authentication.** Each team gets its own Cognito app client
([ADR-008](adr/008-multi-tenant-client-isolation.md)). A client exchanges its
ID and secret for a signed JWT (1-hour TTL, `client_credentials` grant) and
sends it as a Bearer token; the ALB does the rest. Human SSO federates through
Identity Center ([ADR-013](adr/013-identity-center-saml-federation.md)).

Full diagrams live in the
[architecture guide](docs/src/content/docs/developer-guide/architecture.md);
module maps, contracts, and impact analysis live under
[`engineering-docs/`](engineering-docs/README.md).

## Development

Everything runs through [mise](https://mise.jdx.dev/): `mise install` brings
in the pinned toolchain, and every task below is `mise run <task>`.

| Task | What it does |
|------|--------------|
| `install` | Python deps (uv) + git hooks (lefthook) |
| `test` | pytest suite |
| `lint` / `format` | ruff check + format (and terraform fmt) |
| `typecheck` | pyright over `src/` |
| `security` | every scanner below, one command |
| `ci` | lint + typecheck + test + security |
| `tf:plan` / `tf:validate` | Terraform dry-run / validation |
| `docs:serve` / `docs:build` | Starlight docs site, local / production |
| `deps:upgrade` | bump all ecosystems and regenerate every lockfile |

There is no local `dev` server task: the data plane is agentgateway, and its
local loop is the spike compose file
(`docker compose -f spikes/agentgateway-data-plane/docker-compose.yaml up`).

Git hooks (lefthook, all parallel): pre-commit runs ruff, pyright, gitleaks,
hadolint, terraform fmt/validate/docs, SPDX license headers, and actionlint on
what you staged; pre-push runs pytest, semgrep, checkov, trivy, and the
dependency-license gate; commit-msg enforces
[Conventional Commits](https://www.conventionalcommits.org/).

## Security and supply chain

Every gate runs in three places where it applies: locally (`mise run
security`), in git hooks, and in CI. An ignore file entry without a written
reason is treated as a finding.

| Concern | Tools | Enforced where |
|---------|-------|----------------|
| SAST | Semgrep, Bandit, CodeQL | CI (SARIF), pre-push |
| Secrets | Gitleaks | pre-commit, CI |
| IaC | Checkov, TFLint | pre-push, CI |
| Container | Hadolint, Trivy, Amazon Inspector (ECR) | pre-commit, CI, continuous |
| Dependencies | pip-audit, OSV-Scanner (every lockfile, recursively), Dependency Review, Dependabot (5 ecosystems) | CI, PR-time, weekly |
| License policy | `scripts/check-licenses.py`: SPDX-normalized allowlist, copyleft denied by family, exemptions require evidence | CI (fails the build), pre-push |
| License headers | `scripts/check-license-headers.py`: every tracked `.py`/`.sh`/`.tf` carries the SPDX line | CI, pre-commit |
| SBOM | Syft (CycloneDX + SPDX over the source tree: npm, PyPI, and pinned Actions), Grype rescan with `.grype.yaml` | CI artifact, nightly rescan |
| Workflows | actionlint, all Actions pinned to commit SHAs | CI, pre-commit |
| Release integrity | Cosign keyless signing, SLSA build provenance attestations, per-release SBOMs | release workflow |
| Posture | OpenSSF Scorecard | weekly |

A nightly workflow rescans the latest SBOM and image against updated
vulnerability databases, and an advisory-triggered workflow re-runs the scan
when a new CVE lands. Rationale in
[ADR-004](adr/004-security-pipeline-composition.md); disclosure policy in
[SECURITY.md](.github/SECURITY.md).

## Repository map

```text
infrastructure/    Terraform: VPC, ALB, Cognito, ECS, WAF, control plane (see its README)
src/               gwcore + 11 control-plane Lambda services
tests/             pytest suite for src/
clients/           admin CLI (Python) and Codex client config
scripts/           token fetch, onboarding wizard, health check, CI gates
docs/              Starlight documentation site (pnpm)
adr/               17 architectural decision records
engineering-docs/  generated deep-dive reference (module map, contracts, flows)
spikes/            dated proof-of-concept notes (agentgateway data plane)
```

## Contributing

Fork, branch from `main`, make the change, and open a PR. `mise run ci` is
the local equivalent of the pipeline; all checks must pass before merge, and
merge to `main` deploys. Details in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE). Every source file carries an
`SPDX-License-Identifier` line, and dependency licenses are enforced against
an allowlist in CI.
