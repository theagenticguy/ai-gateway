---
title: Code Quality
description: Ruff, pyright, pytest, Terraform quality, and the security scanning stack.
sidebar:
  order: 6
---
AI Gateway enforces code quality through automated linting, type checking, formatting, security scanning, and git hooks. This page documents every tool in the stack and how they are configured.

## Python: Ruff

[Ruff](https://docs.astral.sh/ruff/) handles both linting and formatting. Configuration is in `ruff.toml`.

**Key settings:**

| Setting | Value |
|---------|-------|
| Target version | Python 3.13 |
| Line length | 120 characters |
| Quote style | Double quotes |
| Indent style | Spaces |
| Docstring code format | Enabled |

**Enabled rule sets (30+):**

| Code | Rule Set | What It Catches |
|------|----------|-----------------|
| `E` / `W` | pycodestyle | PEP 8 errors and warnings |
| `F` | pyflakes | Unused imports, undefined names, redefined variables |
| `I` | isort | Import ordering |
| `N` | pep8-naming | Naming convention violations |
| `UP` | pyupgrade | Outdated Python syntax (upgrades to 3.13 idioms) |
| `S` | flake8-bandit | Security issues (assert, exec, eval, hardcoded passwords) |
| `B` | flake8-bugbear | Likely bugs and design problems |
| `A` | flake8-builtins | Shadowing Python builtins |
| `C4` | flake8-comprehensions | Unnecessary list/dict/set comprehensions |
| `DTZ` | flake8-datetimez | Naive datetime usage (missing tzinfo) |
| `T10` | flake8-debugger | Leftover debugger statements |
| `EM` | flake8-errmsg | Exception message formatting |
| `LOG` | flake8-logging | Logging best practices |
| `G` | flake8-logging-format | Logging format string issues |
| `PIE` | flake8-pie | Miscellaneous lint (unnecessary pass, dict comprehension) |
| `PT` | flake8-pytest-style | pytest best practices |
| `RET` | flake8-return | Unnecessary return/else patterns |
| `SIM` | flake8-simplify | Simplifiable code patterns |
| `TCH` | flake8-type-checking | Imports that should be in `TYPE_CHECKING` blocks |
| `ARG` | flake8-unused-arguments | Unused function arguments |
| `PTH` | flake8-use-pathlib | `os.path` usage that should be `pathlib` |
| `ERA` | eradicate | Commented-out code |
| `PL` | pylint | Pylint rules (conventions, refactoring, warnings, errors) |
| `TRY` | tryceratops | Exception handling anti-patterns |
| `FLY` | flynt | String concatenation that should be f-strings |
| `PERF` | perflint | Performance anti-patterns |
| `FURB` | refurb | Modern Python refactoring suggestions |
| `RUF` | ruff-specific | Ruff's own rules (ambiguous characters, mutable defaults) |

**Ignored rules:**

| Rule | Reason |
|------|--------|
| `S101` | Allow `assert` in tests |
| `TRY003` | Allow long exception messages |
| `EM101` / `EM102` | Allow string/f-string literals in exceptions |

**Per-file ignores:**

- `tests/**/*.py`: `S101` (assert), `ARG` (unused arguments in fixtures), `PLR2004` (magic numbers)

**Commands:**

```bash
# Check for violations
mise run lint

# Auto-format and fix
mise run format
```

## Python: Pyright

[Pyright](https://github.com/microsoft/pyright) provides static type checking. Configuration is in `pyrightconfig.json`.

| Setting | Value |
|---------|-------|
| Type checking mode | `standard` |
| Include | `src/` |
| Exclude | `__pycache__`, `tests` |
| Python version | 3.13 |
| Platform | Linux |
| Report missing imports | Yes |
| Report unused imports | Yes |
| Report unused variables | Yes |

**Command:**

```bash
mise run typecheck
```

:::note[Tests are excluded]
Pyright runs against `src/` only. Test files are excluded from type checking to avoid noise from test fixtures and mocking patterns.
:::


## Python: Pytest

[Pytest](https://docs.pytest.org/) is used for testing. Tests live in the `tests/` directory.

**Local execution:**

```bash
# Full test suite (verbose)
mise run test

# Quick check (fail-fast, quiet -- used by pre-push hook)
uv run pytest tests/ -x -q
```

**Conventions:**

- Test files: `tests/test_*.py` or `tests/**/test_*.py`
- Test functions: `def test_*():`
- Fixtures: Defined in `conftest.py` files at the appropriate directory level
- Markers: Use `@pytest.mark.<marker>` for test categorization

**Source watching:** The `test` task in `mise.toml` has `sources = ["src/**/*.py", "tests/**/*.py"]`, so mise can skip re-running if no Python files changed.

## Terraform Quality

### terraform fmt

Enforces the standard HCL formatting. The pre-commit hook checks formatting; `mise run format` auto-fixes it.

```bash
# Check only
terraform -chdir=infrastructure fmt -check -recursive

# Auto-fix
mise run tf:fmt
```

### terraform validate

Validates that all Terraform configuration is syntactically correct and internally consistent.

```bash
mise run tf:validate
```

### TFLint

[TFLint](https://github.com/terraform-linters/tflint) with the AWS ruleset provides Terraform-specific linting. Configuration is in `infrastructure/.tflint.hcl`.

**Enabled rules:**

| Rule | What It Checks |
|------|---------------|
| `terraform_naming_convention` | Consistent naming for resources, variables, outputs |
| `terraform_documented_outputs` | All outputs have descriptions |
| `terraform_documented_variables` | All variables have descriptions |
| `terraform_typed_variables` | All variables have explicit types |
| `terraform_unused_declarations` | No unused variables, locals, or data sources |
| AWS ruleset (v0.38.0) | AWS-specific rules (valid instance types, regions, etc.) |

**Disabled rules:**

| Rule | Reason |
|------|--------|
| `terraform_standard_module_structure` | Module structure is intentionally simplified (no `versions.tf` per child module) |

### Checkov

[Checkov](https://www.checkov.io/) scans Terraform for security misconfigurations against 2,500+ policies. Results are uploaded as SARIF to the GitHub Security tab.

```bash
mise run security:iac
```

### terraform-docs

[terraform-docs](https://terraform-docs.io/) auto-generates documentation for the infrastructure module. The pre-commit hook regenerates and stages the README; CI verifies it is up to date.

```bash
mise run tf:docs
```

Configuration in `.terraform-docs.yml`:

- Output format: Markdown table
- Sections: header, requirements, providers, modules, resources, inputs, outputs
- Sort: By required status
- Injection mode: Updates existing `infrastructure/README.md` in-place

## Git Hooks (Lefthook)

[Lefthook](https://github.com/evilmartians/lefthook) manages all git hooks. Configuration is in `lefthook.yml`.

### Pre-commit (10 parallel checks)

| Hook | Glob Filter | Auto-stages Fixes |
|------|-------------|-------------------|
| ruff lint (`--fix`) | `*.py` | Yes |
| ruff format | `*.py` | Yes |
| pyright | `*.py` | No |
| gitleaks protect | All staged | No |
| hadolint | `Dockerfile*` | No |
| terraform fmt (check) | `infrastructure/**/*.tf` | No |
| terraform validate | `infrastructure/**/*.tf` | No |
| terraform-docs | `infrastructure/**/*.tf` | Yes |
| license headers (`scripts/check-license-headers.py`) | `*.{py,sh,tf}` | No |
| actionlint | `.github/workflows/*.{yml,yaml}` | No |

### Pre-push (5 parallel checks)

| Hook | Scope |
|------|-------|
| pytest (`-x -q`) | `tests/` (fail-fast) |
| semgrep | Full repo (OWASP Top 10, quiet) |
| checkov | `infrastructure/` (compact, quiet) |
| trivy fs | Full repo (HIGH + CRITICAL, quiet) |
| license policy (`scripts/check-licenses.py`) | Runs when `uv.lock` or `pyproject.toml` changed |

### Commit-msg

Validates Conventional Commits format: `<type>(<scope>): <description>` with max 72-character first line.

## CODEOWNERS

The `.github/CODEOWNERS` file enforces review requirements:

```
# Default owner for everything
* @theagenticguy

# Infrastructure requires explicit review
infrastructure/ @theagenticguy
```

All PRs require review from `@theagenticguy`. Infrastructure changes have an additional explicit rule to ensure they are always reviewed.

## Security Scanning Stack

The project uses 19 security tools across development, CI, and deployment phases.

| Tool | Category | What It Covers | Where It Runs |
|------|----------|---------------|---------------|
| [Semgrep](https://semgrep.dev/) | SAST | Python code analysis (OWASP Top 10, security audit) | Pre-push hook, CI |
| [Bandit](https://bandit.readthedocs.io/) | SAST | Python security linting (SARIF upload, report-only) | CI |
| [Gitleaks](https://gitleaks.io/) | Secrets | Prevents secrets from entering the repository | Pre-commit hook, CI |
| [Checkov](https://www.checkov.io/) | IaC | Terraform security and compliance (2,500+ policies) | Pre-push hook, CI |
| [Hadolint](https://github.com/hadolint/hadolint) | Dockerfile | Dockerfile best practices with ShellCheck integration | Pre-commit hook, CI |
| [Trivy](https://trivy.dev/) | Container + FS | Vulnerability scanning of images and filesystem (HIGH + CRITICAL) | Pre-push hook, CI |
| [pip-audit](https://github.com/pypa/pip-audit) | Dependencies | Audits locked Python dependencies for known vulnerabilities | `mise run security:deps`, CI |
| [OSV-Scanner](https://google.github.io/osv-scanner/) | Dependencies | Scans every lockfile in the tree (including `clients/admin_cli/uv.lock`) against the OSV database | `mise run security:osv`, CI, nightly rescan |
| [Syft](https://github.com/anchore/syft) | SBOM | CycloneDX + SPDX SBOM generation for the source tree and the container image | `mise run security:sbom`, CI, Release |
| [Grype](https://github.com/anchore/grype) | SBOM rescan | Scans the source SBOM for vulnerabilities; accepted findings live in `.grype.yaml` | `mise run security:sbom-rescan`, CI, nightly rescan |
| License policy (`scripts/check-licenses.py`) | Licenses | Fails on a dependency license not on the allowlist (denies GPL/AGPL/LGPL/SSPL families) | Pre-push hook, `mise run security:licenses`, CI |
| License headers (`scripts/check-license-headers.py`) | Licenses | Every tracked `.py`/`.sh`/`.tf` file carries `SPDX-License-Identifier: Apache-2.0` | Pre-commit hook, `mise run security:headers`, CI |
| [actionlint](https://github.com/rhysd/actionlint) | Workflows | Lints GitHub Actions workflow files (shellcheck included) | Pre-commit hook, `mise run security:actionlint`, CI |
| [Cosign](https://github.com/sigstore/cosign) | Signing | Keyless image signing via Sigstore OIDC | Release |
| [CodeQL](https://codeql.github.com/) | Code analysis | GitHub-native semantic code analysis (SARIF upload) | CI, Weekly schedule |
| [OpenSSF Scorecard](https://scorecard.dev/) | Supply chain | Supply chain security posture assessment | CI (main push), Weekly |
| [Dependency Review](https://github.com/actions/dependency-review-action) | Dependencies | PR-time vulnerability and license check (denies GPL-3.0, AGPL-3.0) | PR only |
| [Dependabot](https://docs.github.com/en/code-security/dependabot) | Dependencies | Automated updates for Python, Terraform, GitHub Actions, npm, and Docker | Weekly Monday 08:00 ET |
| [TFLint](https://github.com/terraform-linters/tflint) | IaC | Terraform linting with AWS ruleset | CI |

:::caution[Trivy supply chain advisory]
CVE-2026-28353 affected the trivy Go module (Feb-Mar 2026). The CLI binary was unaffected. Mitigation: pin trivy versions in `mise.toml` and verify checksums. See [ADR-004](../adrs/004-security-pipeline-composition.md) for details.
:::


## EditorConfig

The `.editorconfig` file ensures consistent formatting across editors:

| File Pattern | Indent | Size | Line Length |
|-------------|--------|------|-------------|
| `*.py` | Spaces | 4 | 120 |
| `*.tf` | Spaces | 2 | -- |
| `*.{toml,yaml,yml}` | Spaces | 2 | -- |
| `Dockerfile*` | Spaces | 4 | -- |
| `Makefile` | Tabs | -- | -- |

**Global settings** (all files):

- Line endings: LF (`end_of_line = lf`)
- Final newline: Yes
- Trailing whitespace: Trimmed
- Charset: UTF-8