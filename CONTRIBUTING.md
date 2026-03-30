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

### Updating the Portkey Gateway Version

1. Update `PORTKEY_VERSION` in `versions.env`
2. Update the default in `infrastructure/variables.tf` to match
3. Open a PR — CI will pull and scan the new image

### Updating Dev Tool Versions

Tool versions are pinned in `mise.toml`. To upgrade:

```bash
mise ls                   # See current versions
# Edit mise.toml with new version
mise install              # Install the updated version
mise run ci:validate      # Verify everything works
```

### Release Process

This project uses [git-cliff](https://git-cliff.org/) for changelog generation and semver tagging:

```bash
mise run release:bump-patch   # 0.1.0 → 0.1.1
mise run release:bump-minor   # 0.1.0 → 0.2.0
mise run release:bump-major   # 0.1.0 → 1.0.0
git push origin main --tags   # Triggers release workflow
```

The release workflow (`release.yml`) builds, signs, and publishes the container image to ECR with an SBOM.

## Security

- Run `mise run security` before submitting PRs that touch application code
- Never commit secrets, API keys, or credentials — use AWS Secrets Manager
- Report vulnerabilities via [GitHub Security Advisories](https://github.com/theagenticguy/ai-gateway/security/advisories), not public issues (see [SECURITY.md](.github/SECURITY.md))

## Code Style

- Python: enforced by [ruff](https://docs.astral.sh/ruff/) (linting + formatting) and [pyright](https://github.com/microsoft/pyright) (type checking)
- Terraform: enforced by `terraform fmt`
- Git hooks via [lefthook](https://github.com/evilmartians/lefthook) run checks automatically on commit

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
