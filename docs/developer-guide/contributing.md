# Contributing

## Workflow

AI Gateway uses a fork-and-branch workflow:

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally and run `mise run install`.
3. **Create a feature branch** from `main` (e.g., `feat/bedrock-streaming`).
4. **Make your changes** and commit using Conventional Commits format.
5. **Push** your branch and open a pull request against `main`.
6. **All CI checks must pass** and a CODEOWNERS review is required before merge.

## Conventional Commits

Every commit message must follow the [Conventional Commits](https://www.conventionalcommits.org/) format. The `commit-msg` git hook enforces this automatically.

```
<type>(<scope>): <description>
```

**Supported types:**

| Type | When to Use |
|------|------------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation changes only |
| `style` | Formatting, whitespace (no logic change) |
| `refactor` | Code restructuring (no feature change, no bug fix) |
| `perf` | Performance improvement |
| `test` | Adding or updating tests |
| `build` | Build system or external dependency changes |
| `ci` | CI/CD pipeline changes |
| `chore` | Maintenance tasks (tooling, config) |
| `revert` | Reverting a previous commit |

**Examples:**

```bash
git commit -m "feat(auth): add Cognito custom scope validation"
git commit -m "fix(compute): correct OTel sidecar memory limit"
git commit -m "docs(adr): add ADR-008 for rate limiting strategy"
git commit -m "ci: upgrade trivy action to v0.36"
```

!!! warning "Scope is optional but encouraged"
    The `(<scope>)` part is optional. When used, it should reference the module or area being changed: `auth`, `compute`, `networking`, `observability`, `ci`, `docs`, `adr`, etc.

## Git Hooks

[Lefthook](https://github.com/evilmartians/lefthook) manages git hooks. All hooks within each stage run in parallel for speed.

### Pre-commit (runs on every commit)

| Check | Scope | Auto-fixes |
|-------|-------|------------|
| ruff lint | `*.py` staged files | Yes (stages fixed files) |
| ruff format | `*.py` staged files | Yes (stages fixed files) |
| pyright | `src/` | No |
| gitleaks | Staged changes | No |
| hadolint | `Dockerfile*` staged files | No |
| terraform fmt | `infrastructure/**/*.tf` | No (check only) |
| terraform validate | `infrastructure/**/*.tf` | No |
| terraform-docs | `infrastructure/**/*.tf` | Yes (regenerates and stages README) |

### Pre-push (runs before push)

| Check | Scope |
|-------|-------|
| pytest | `tests/` (fail-fast mode) |
| semgrep | Full repository (OWASP Top 10 rules) |
| checkov | `infrastructure/` (Terraform framework) |
| trivy fs | Full repository (HIGH + CRITICAL) |

### Commit-msg

Validates that the commit message matches Conventional Commits format. Rejects commits that do not match.

## Running Quality Gates Locally

Use `mise run` to execute any project task. The most common workflows:

```bash
# Run the full CI pipeline locally (lint + typecheck + test + security)
mise run ci

# Individual checks
mise run lint          # ruff check + format check
mise run typecheck     # pyright on src/
mise run test          # pytest on tests/
mise run security      # all security scans (SAST, secrets, IaC, Dockerfile, trivy fs)

# Format code (auto-fix)
mise run format        # ruff format + ruff check --fix + terraform fmt

# Terraform operations
mise run tf:validate   # terraform init + validate
mise run tf:plan       # terraform init + plan
mise run tf:fmt        # terraform fmt -recursive
mise run tf:docs       # regenerate infrastructure/README.md
```

## Project Task Reference

All tasks are defined in `mise.toml` and run with `mise run <task>`.

### Core Tasks

| Task | Description |
|------|-------------|
| `install` | Install all project dependencies and git hooks |
| `dev` | Run the API gateway in development mode (uvicorn, port 8000) |
| `test` | Run test suite with pytest |
| `lint` | Run ruff linter and format check |
| `format` | Auto-format Python (ruff) and Terraform (fmt) |
| `typecheck` | Run pyright type checker on `src/` |

### Security Tasks

| Task | Description |
|------|-------------|
| `security` | Run all security scans (depends on all sub-tasks below) |
| `security:sast` | SAST scan with semgrep (OWASP Top 10, security audit) |
| `security:secrets` | Secret detection with gitleaks |
| `security:iac` | IaC security scan with checkov (Terraform framework) |
| `security:dockerfile` | Lint Dockerfiles with hadolint |
| `security:image` | Scan container image with trivy (HIGH + CRITICAL) |
| `security:fs` | Filesystem vulnerability scan with trivy |

### Terraform Tasks

| Task | Description |
|------|-------------|
| `tf:init` | Initialize Terraform |
| `tf:plan` | Terraform plan (depends on `tf:init`) |
| `tf:fmt` | Format Terraform files recursively |
| `tf:validate` | Validate Terraform configuration (depends on `tf:init`) |
| `tf:docs` | Generate Terraform documentation with terraform-docs |

### Terragrunt Tasks

| Task | Description |
|------|-------------|
| `tg:init` | Terragrunt init for a specific environment |
| `tg:plan` | Terragrunt plan for a specific environment |
| `tg:plan-all` | Terragrunt plan all environments |
| `tg:validate-all` | Terragrunt validate all environments |

### CI Tasks

| Task | Description |
|------|-------------|
| `ci` | Full CI pipeline (lint, typecheck, test, security) |
| `ci:lint` | Validate GitHub Actions workflows with actionlint |
| `ci:validate` | Validate all CI + quality gates in one shot |

### Documentation Tasks

| Task | Description |
|------|-------------|
| `docs:serve` | Serve documentation locally with hot reload |
| `docs:build` | Build documentation site |

## Pull Request Requirements

Before a PR can be merged:

1. **All CI jobs must pass** -- quality, SAST, IaC security, and container security.
2. **CODEOWNERS review** -- `@theagenticguy` is the default owner for all files. Infrastructure changes (`infrastructure/`) require explicit review.
3. **Dependency review** -- The dependency-review workflow blocks PRs that introduce HIGH/CRITICAL vulnerabilities or GPL-3.0/AGPL-3.0 licensed dependencies.
4. **Conventional commit messages** -- Every commit in the PR must follow the format.

## Adding New Terraform Modules

Follow the existing module pattern:

1. Create a directory under `infrastructure/modules/<module-name>/`.
2. Add three files following the standard structure:
    - `variables.tf` -- Input variables with descriptions and types.
    - `main.tf` -- Resource definitions.
    - `outputs.tf` -- Output values with descriptions.
3. Wire the module in `infrastructure/main.tf` with explicit dependency ordering.
4. Add any new root-level variables in `infrastructure/variables.tf`.
5. Run `mise run tf:docs` to regenerate the infrastructure README.
6. Run `mise run tf:validate` to confirm the module is valid.

!!! info "Module dependency order"
    The root module wires modules with explicit data dependencies: observability (first, creates KMS + log groups) -> networking (needs KMS) -> auth (needs ALB) -> compute (needs subnets, ALB, log groups). New modules should be placed in this chain based on their dependencies.

## Adding New Python Code

- Place source code in `src/` and tests in `tests/`.
- Follow the ruff configuration in `ruff.toml` (30+ rule sets enabled, 120-char line length, Python 3.13 target).
- Add type hints to all function signatures -- pyright runs in `standard` mode.
- Write tests using pytest. Use fixtures for shared setup and markers for test categorization.
- Add new dependencies with `uv add <package>` (or `uv add --dev <package>` for dev-only).
