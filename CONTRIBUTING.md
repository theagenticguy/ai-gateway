# Contributing to AI Gateway

Thank you for your interest in contributing to AI Gateway! This document provides guidelines and instructions for contributing.

## Getting Started

1. Fork the repository and clone your fork
2. Install prerequisites (see [README](README.md#prerequisites))
3. Set up the development environment:

```bash
mise install          # Install all tool versions
uv sync               # Install Python dependencies
lefthook install      # Install git hooks
```

## Development Workflow

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature main
   ```
2. Make your changes
3. Run the quality checks:
   ```bash
   mise run lint        # Linter + formatter check
   mise run typecheck   # Type checking
   mise run test        # Test suite
   ```
4. Commit using [Conventional Commits](https://www.conventionalcommits.org/) style:
   ```
   feat: add new provider routing
   fix: correct JWT validation edge case
   docs: update architecture diagram
   ci: improve security scanning step
   ```
5. Push your branch and open a Pull Request against `main`

## Branch Naming

Use descriptive prefixes:

| Prefix | Purpose |
|--------|---------|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `docs/` | Documentation only |
| `ci/` | CI/CD changes |
| `refactor/` | Code restructuring |
| `security/` | Security improvements |
| `infra/` | Infrastructure (Terraform) changes |

## Pull Requests

- Fill out the PR template completely
- PRs require at least one approving review before merge
- All CI checks must pass (lint, typecheck, test, security scans)
- Keep PRs focused — one logical change per PR

## Infrastructure Changes

For Terraform changes:

```bash
mise run tf:fmt        # Format Terraform files
mise run tf:validate   # Validate configuration
mise run security:iac  # Run Checkov IaC scan
```

Include `terraform plan` output in your PR description for infrastructure changes.

## Version Management

All pinned image versions live in `versions.env` at the repo root. CI workflows and Terragrunt read from this file automatically.

### Updating the data-plane (agentgateway) image

1. Bump `AGENTGATEWAY_REF` and `AGENTGATEWAY_IMAGE_DIGEST` together in `versions.env`
2. Update the `gateway_image` default in `infrastructure/variables.tf` to match
3. Open a PR — CI will pull, re-tag by digest, and scan the new image

### Updating Dev Tool Versions

Tool versions are pinned in `mise.toml`. To upgrade:

```bash
mise ls                   # See current versions
# Edit mise.toml with new version
mise install              # Install the updated version
mise run ci:validate      # Verify everything works
```

### Release Process

This project uses [commitizen](https://commitizen-tools.github.io/commitizen/) for changelog generation and semver tagging. Version bumps are auto-detected from commit prefixes (`feat` → minor, `fix` → patch, `BREAKING CHANGE` → major):

```bash
mise run release:bump         # Auto-detect bump type from commits
mise run release:bump-patch   # Force patch: 0.1.0 → 0.1.1
mise run release:bump-minor   # Force minor: 0.1.0 → 0.2.0
mise run release:bump-major   # Force major: 0.1.0 → 1.0.0
mise run release:changelog    # Preview unreleased changes (dry-run)
git push origin main --tags   # Triggers release workflow
```

The release workflow (`release.yml`) builds, signs, and publishes the container image to ECR with an SBOM.

## Security

- Run `mise run security` before submitting PRs that touch application code. The umbrella task runs SAST, secrets, IaC, Dockerfile, filesystem, and dependency scans plus the supply-chain gates: `security:osv` (recursive OSV scan of every lockfile in the tree, including `clients/admin_cli/uv.lock`), `security:sbom` and `security:sbom-rescan` (Syft writes CycloneDX + SPDX SBOMs into the gitignored `sbom/`; Grype rescans them using `.grype.yaml`), `security:licenses` (enforcing dependency-license allowlist in `scripts/check-licenses.py`), `security:headers` (SPDX license headers on every tracked `.py`/`.sh`/`.tf` file via `scripts/check-license-headers.py`), and `security:actionlint` (workflow lint)
- Dependency licenses are an enforced gate. A new dependency must carry a license on the allowlist in `scripts/check-licenses.py`; strong and network copyleft (GPL, AGPL, LGPL, SSPL, and related families) is denied by family. The `licenses` pre-push hook and the CI `licenses` job both fail on a violation
- Never commit secrets, API keys, or credentials — use AWS Secrets Manager
- Report vulnerabilities via [GitHub Security Advisories](https://github.com/theagenticguy/ai-gateway/security/advisories), not public issues (see [SECURITY.md](.github/SECURITY.md))

## Code Style

- Python: enforced by [ruff](https://docs.astral.sh/ruff/) (linting + formatting) and [pyright](https://github.com/microsoft/pyright) (type checking)
- Terraform: enforced by `terraform fmt`
- Every tracked `.py`, `.sh`, and `.tf` file carries an `SPDX-License-Identifier: Apache-2.0` line near the top; `scripts/check-license-headers.py` enforces this at pre-commit and in CI
- Git hooks via [lefthook](https://github.com/evilmartians/lefthook) run checks automatically. Pre-commit runs lint, format, typecheck, secrets, hadolint, terraform fmt/validate/docs, license headers, and actionlint on workflow files; pre-push runs tests, semgrep, checkov, trivy, and the license policy check

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
