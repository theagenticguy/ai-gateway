---
title: Developer Guide
description: Contribute to the project, understand the architecture, and run CI locally.
sidebar:
  order: 1
---
This section is for contributors and developers who want to modify, extend, or understand the internals of AI Gateway. Whether you are adding a new Terraform module, fixing a bug, or improving the CI pipeline, start here.

## What This Section Covers

| Page | Description |
|------|-------------|
| [Contributing](contributing.md) | Fork-and-branch workflow, commit conventions, PR requirements, and the full mise task reference |
| [Architecture](architecture.md) | System architecture with Mermaid diagrams, module boundaries, request and auth flows |
| [ADR Index](adr-index.md) | All 7 Architecture Decision Records with summaries and rationale |
| [CI/CD Pipeline](ci-cd.md) | The 6-job CI pipeline, additional workflows, release process, and Dependabot config |
| [Code Quality](code-quality.md) | Ruff, pyright, pytest, Terraform quality gates, git hooks, and the 12-tool security scanning stack |

## Quick Development Setup

```bash
# 1. Clone the repository
git clone git@github.com:theagenticguy/ai-gateway.git
cd ai-gateway

# 2. Install all tool versions (Python 3.13, Terraform 1.10.5, lefthook, etc.)
mise install

# 3. Install Python dependencies and git hooks
mise run install
# (equivalent to: uv sync && lefthook install)

# 4. Verify everything works
mise run ci
```

:::tip[One-command setup]
`mise run install` handles both Python dependency installation (`uv sync`) and git hook registration (`lefthook install`) in a single step.
:::


After setup, your environment includes:

- **Python 3.13** with a `.venv` managed by uv
- **Terraform 1.10.5** with all providers pinned in `versions.tf`
- **lefthook** git hooks (pre-commit, pre-push, commit-msg)
- **Security tools**: trivy, hadolint, gitleaks, checkov (all installed via mise)

## Project Layout

```
ai-gateway/
  adr/                    # Architecture Decision Records (001-007)
  docs/                   # Documentation source (Zensical/MkDocs)
  infrastructure/         # Terraform root module + 4 child modules
    modules/
      auth/               # Cognito, JWT listener
      compute/            # ECS, ECR, IAM, Secrets Manager
      networking/         # VPC, ALB, WAF
      observability/      # KMS, CloudWatch log groups, dashboard
    environments/         # Per-environment tfvars (dev, prod)
  scripts/                # Operational scripts (token retrieval, CW queries)
  .github/
    workflows/            # CI/CD, CodeQL, dependency-review, release, scorecard, docs
    dependabot.yml        # Automated dependency updates
    CODEOWNERS            # Review requirements
    SECURITY.md           # Vulnerability reporting policy
  mise.toml               # Tool versions + 25+ project tasks
  lefthook.yml            # Git hook definitions
  pyproject.toml          # Python project metadata + dev dependencies
  ruff.toml               # Linter/formatter configuration
  pyrightconfig.json      # Type checker configuration
  zensical.toml           # Documentation site configuration
```