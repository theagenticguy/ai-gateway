---
title: Developer Tooling Domain -- Research Report
description: Python tooling, security pipeline, git hooks, and dev tool management research.
sidebar:
  order: 2
---
**Project**: AI Gateway (LLM API Gateway)
**Date**: 2026-03-18
**Researcher Domain**: DevTools
**Stack Context**: Python 3.13, uv, ECS Fargate, Terraform, Portkey OSS

---

## Table of Contents

1. [Python Tooling (Defaults -- Health Checks)](#1-python-tooling)
2. [Security Pipeline (Must-Haves + Research)](#2-security-pipeline)
3. [Git Hooks (Default -- Health Check)](#3-git-hooks)
4. [Dev Tool Management (Default -- Health Check)](#4-dev-tool-management)
5. [Domain-Specific Artifacts](#5-domain-specific-artifacts)
6. [Developer Workflow](#6-developer-workflow)
7. [Compatibility Notes](#7-compatibility-notes)
8. [Sources](#8-sources)

---

## 1. Python Tooling

All four Python tools are opinionated defaults. Health checks only.

### 1.1 Linter + Formatter: ruff -- Default Confirmed

**Recommendation: ruff**

- **Version**: 0.15.6 (released 2026-03-12)
- **Why**: ruff replaces black, isort, flake8, pylint, and dozens of plugins in a single Rust binary. At v0.15.x it supports Python 3.15 features, lazy imports, and the 2026 formatter style guide. Weekly release cadence demonstrates exceptional maintenance.
- **Health**: HEALTHY

#### Health Check

- **Version**: 0.15.6 (released 2026-03-12) -- 6 days ago
- **Activity**: Weekly releases (0.15.5 on Mar 5, 0.15.6 on Mar 12). Extremely active.
- **Maintainers**: Astral team (corporate-backed, funded). 9+ contributors per release.
- **Stars**: 46,300 | **License**: MIT
- **CVEs**: None known
- **Notes**: Astral (creators of uv and ruff) is well-funded and fully committed to the Python ecosystem. The v0.15.0 release (Feb 2026) introduced the 2026 style guide for the formatter.

---

### 1.2 Type Checker: pyright -- Default Confirmed

**Recommendation: pyright**

- **Version**: 1.1.408 (released 2026-01-08)
- **Why**: Pyright is the fastest Python type checker, with deep VSCode/Pylance integration. Backed by Microsoft. The PyPI wrapper package allows installation without Node.js. Snap updated Feb 18, 2026.
- **Health**: HEALTHY

#### Health Check

- **Version**: 1.1.408 (released 2026-01-08) -- 2 months ago
- **Activity**: GitHub release cadence has slowed slightly (was monthly, now ~2-3 months), but the snap package was updated Feb 18. The tool is mature and stable.
- **Maintainers**: Microsoft (bschnurr + team). Corporate-backed.
- **Stars**: 15,300 | **License**: MIT
- **CVEs**: None known
- **Notes**: basedpyright exists as a community fork that adds Pylance-exclusive features. For this project, standard pyright is sufficient. Default Python version changed to 3.14 in recent releases.

---

### 1.3 Package Manager: uv -- Locked In (Skip)

**Status**: Locked in by user. Compatibility validated.

- **Version**: 0.10.11 (released 2026-03-16) -- 2 days ago
- **Activity**: Multiple releases per week. 81,200 stars. 124M monthly PyPI downloads.
- **Maintainers**: Astral (corporate-backed)
- **License**: MIT OR Apache-2.0
- **Health**: HEALTHY
- **Notes**: uv 0.10.10 added preview `uv audit` (OSV-based dependency vulnerability scanning). This may reduce the need for a separate osv-scanner. 2,700 forks, massive community.

---

### 1.4 Testing: pytest -- Default Confirmed

**Recommendation: pytest**

- **Version**: 9.0.2 (released ~March 2026)
- **Why**: Universal Python testing standard. v9.0 added subtests, native TOML config, and unified strict mode. Requires Python 3.10+.
- **Health**: HEALTHY

#### Health Check

- **Version**: 9.0.2 (March 2026)
- **Activity**: Major version 9.0 released recently. Active development.
- **Maintainers**: Core team + large open-source community. 10M+ monthly PyPI downloads.
- **Stars**: 12,000+ | **License**: MIT
- **CVEs**: None known
- **Notes**: pytest 9.0 is a significant release with subtests and TOML config support.

---

## 2. Security Pipeline

The user has must-haves (checkov, trivy, hadolint) and the domain config lists additional defaults (semgrep, gitleaks, grype, osv-scanner). Research required on overlap and composition.

### 2.1 IaC Security: checkov -- Must-Have (Health Check)

**Recommendation: checkov**

- **Version**: 3.2.508 (released 2026-03-08)
- **Why**: 2,500+ built-in policies covering Terraform, CloudFormation, Kubernetes, Helm, Dockerfiles, and more. Graph-based cross-resource analysis catches issues that single-file scanners miss. Backed by Palo Alto Networks (acquired Bridgecrew).
- **Health**: HEALTHY

#### Health Check

- **Version**: 3.2.508 (released 2026-03-08) -- 10 days ago
- **Activity**: Multiple releases per month (3.2.506 on Feb 23, 3.2.507 on Mar 5, 3.2.508 on Mar 8). Very active.
- **Maintainers**: Palo Alto Networks / Prisma Cloud team. Corporate-backed.
- **Stars**: 8,500 | **Forks**: 1,300 | **License**: Apache 2.0
- **CVEs**: None known
- **PyPI downloads**: 30.2M/month
- **Notes**: Checkov 3.x supports Python 3.9+. It scans Terraform HCL, Terraform plan JSON, Dockerfiles, and Kubernetes manifests. Also has built-in secrets detection. Pairs well with `checkov-action@v12` for GitHub Actions.

---

### 2.2 Multi-Scanner: trivy -- Must-Have (Health Check with Advisory)

**Recommendation: trivy**

- **Version**: v0.69.2 (released ~March 2026, post-incident patch)
- **Why**: The most comprehensive open-source security scanner -- covers container images, filesystems, IaC, secrets, and licenses in a single binary. Absorbed tfsec. 31,700+ stars, 513+ contributors.
- **Health**: CAUTION

#### Health Check

- **Version**: v0.69.2 (post-incident release, March 2026)
- **Activity**: Extremely active. 178+ releases. Multiple releases per month.
- **Maintainers**: Aqua Security (corporate-backed). 513+ contributors.
- **Stars**: 32,200 | **License**: Apache 2.0
- **CVEs**: **CVE-2026-28353** -- GitHub Actions `pull_request_target` misconfiguration led to PAT compromise on Feb 27, 2026. Attacker deleted 178 releases, published malicious VSCode extension, renamed the repo temporarily. The core Trivy CLI binary and scanning engine were NOT compromised.
- **Incident details**: The vulnerability was in Trivy's GitHub Actions CI workflow, not in the scanner itself. Aqua Security responded by revoking tokens, fixing the workflow (PR #10259), and releasing v0.69.2. A further v0.69.3 was released with additional fixes.
- **Assessment**: The incident is concerning for supply chain trust but does NOT affect the CLI binary when installed via package managers (brew, apt) or built from source. The scanner's vulnerability database and scanning logic remain unaffected. The team's incident response was fast (same day).

**Mitigation recommendations**:
1. Pin trivy to specific versions in CI (not `latest`)
2. Verify checksums on all binary downloads
3. Use `gh attestation verify` for GitHub artifact attestations
4. Do NOT install the Trivy VSCode extension from OpenVSIX
5. Pull the container image from a trusted registry and pin the digest

---

### 2.3 Dockerfile Linting: hadolint -- Must-Have (Health Check)

**Recommendation: hadolint**

- **Version**: v2.14.0 (released 2025-09-22)
- **Why**: The only dedicated Dockerfile linter. Parses Dockerfiles into an AST and applies best-practice rules. Integrates ShellCheck for linting RUN instructions. User explicitly requires it.
- **Health**: CAUTION

#### Health Check

- **Version**: v2.14.0 (released 2025-09-22) -- 6 months ago
- **Activity**: After a nearly 3-year gap (v2.12.0 in Nov 2022 to v2.13.1 in Sep 2025), two releases shipped in September 2025. Last push to master was 2026-03-09, so there is ongoing activity.
- **Maintainers**: Small open-source team (m-ildefons is primary). No corporate backing.
- **Stars**: 12,000 | **License**: GPL-3.0
- **Open Issues**: 255
- **CVEs**: None known
- **Notes**: The "Is the project alive?" issue (#1110) was opened Aug 2025 and then closed after the v2.13.1/v2.14.0 releases. The project shows signs of resumed maintenance. GPL-3.0 license is fine for a CLI tool (does not affect your project's license). There is no alternative Dockerfile linter of comparable quality. Trivy's misconfig scanner covers some Dockerfile rules but is less thorough on Dockerfile-specific best practices and ShellCheck integration.

---

### 2.4 SAST: semgrep -- Default (Health Check)

**Recommendation: semgrep (Community Edition)**

- **Version**: v1.152.0 (released ~March 2026)
- **Why**: Language-agnostic SAST with 3,000+ community rules. The `p/python` and `p/owasp-top-ten` rulesets are highly relevant for an API gateway handling authentication tokens and upstream credentials. Single-file analysis in CE is sufficient for this project's needs.
- **Health**: HEALTHY

#### Health Check

- **Version**: v1.152.0 (March 2026)
- **Activity**: Monthly release notes. Active development on both OSS and platform.
- **Maintainers**: Semgrep Inc (formerly r2c). Corporate-backed, well-funded.
- **Stars**: 11,000+ | **License**: LGPL-2.1 (CE)
- **CVEs**: None known
- **Notes**: February 2026 release added `--x-mem-policy` for garbage collector tuning, case-insensitive string comparisons in rules, and MCP server DNS rebinding protection. The CE (Community Edition) is free and sufficient. The LGPL-2.1 license is fine for a CLI tool.

**Recommended rulesets for this project**:
- `p/python` -- Python-specific security and correctness
- `p/owasp-top-ten` -- OWASP Top 10 coverage
- `p/security-audit` -- Broad security patterns
- `p/dockerfile` -- Dockerfile security patterns (complements hadolint)

---

### 2.5 Secret Detection: gitleaks / betterleaks -- Default with Transition Note

**Recommendation: gitleaks (with betterleaks migration path)**

- **Version**: gitleaks v8.x (current stable); betterleaks launched 2026-03-12
- **Why**: gitleaks is the most widely adopted open-source secret scanner (26M GitHub downloads, 35M Docker pulls, 160+ built-in detectors). However, the original author (Zach Rice) has lost full control of the gitleaks repo and launched betterleaks as a drop-in successor with faster scanning, configurable validation, and AI-agent-ready features.
- **Health**: CAUTION (gitleaks -- governance transition); betterleaks is too new to assess

#### Health Check -- gitleaks

- **Version**: v8.x (latest stable)
- **Activity**: The original author no longer has full control of the repository.
- **Maintainers**: Governance concern -- creator has moved to betterleaks.
- **Stars**: 19,000+ | **License**: MIT
- **CVEs**: None known
- **Notes**: gitleaks still works and is widely used. The configuration format and CLI options are stable. For now, use gitleaks. Watch betterleaks (backed by Aikido Security) for maturity. betterleaks is a drop-in replacement with identical CLI options and config format compatibility.

#### Health Check -- betterleaks

- **Version**: Initial release (2026-03-12) -- too new
- **Activity**: Just launched, 4 maintainers committed.
- **Maintainers**: Zach Rice (original gitleaks author) + 3 core contributors. Backed by Aikido Security.
- **License**: MIT
- **Notes**: Drop-in replacement for gitleaks. Same config format, same CLI flags. Adds: faster scanning, configurable validation, new filter options. Designed for AI agent workflows. Too new for production recommendation, but the migration path is trivial when ready.

**Recommendation**: Start with gitleaks now. Plan migration to betterleaks in Q3 2026 once it has a few months of production usage in the community.

---

### 2.6 Container Vulnerability Scanning: grype vs trivy -- Full Research

**Research Question**: Should grype be added alongside trivy, or does trivy cover everything?

#### Comparison Matrix

| Criteria (weight) | trivy | grype |
|---|---|---|
| Coverage breadth (0.25) | 9/10 -- containers, fs, IaC, secrets, licenses, K8s | 7/10 -- containers, fs, SBOM only |
| Vulnerability detection (0.25) | 8/10 -- NVD, vendor feeds, OSV | 9/10 -- NVD, vendor feeds, EPSS scoring, composite risk |
| False positive rate (0.15) | 7/10 -- higher volume of findings | 8/10 -- lower false positives, better matching |
| CI/CD integration (0.15) | 9/10 -- trivy-action, SARIF, JSON, table | 8/10 -- JSON, table, CycloneDX, SARIF |
| EPSS/KEV scoring (0.10) | 7/10 -- basic CVSS | 9/10 -- EPSS + KEV + composite risk scoring |
| Maintenance (0.10) | 9/10 -- Aqua Security, very active | 9/10 -- Anchore, very active (v0.92+ in March 2026) |
| **Weighted Score** | **8.15** | **8.10** |

#### Evidence

- LinkedIn analysis (Dec 2025): Across 4 Docker images, grype found 212 CVEs vs trivy's 143 -- a 48% difference, with grype detecting more CVEs in binary components and musl/libcrypto [4].
- AppSec Santa (Feb 2026): "Trivy and Grype lead the open-source SCA space -- Trivy scans containers, filesystems, and IaC in a single binary, while Grype focuses purely on vulnerability matching with lower false positives" [5].
- Grype v0.92+ (March 2026): Ships EPSS scoring (probability of exploitation in next 30 days) and KEV integration in table output [6].

#### Recommendation: Use trivy as primary, skip grype

For a small team (2-5 people), adding grype alongside trivy creates tool sprawl without proportional benefit. Trivy already covers container images, filesystem scanning, IaC misconfig (absorbed tfsec), secrets, and licenses. The areas where grype outperforms trivy (EPSS scoring, binary component detection) are valuable for large security teams doing triage at scale, but for this project's medium scale, trivy's all-in-one approach is the right tradeoff.

If you later find trivy's container scanning insufficient, grype is the tool to add. But start with trivy only.

---

### 2.7 Dependency Vulnerability Scanning: osv-scanner -- Assessment

**Research Question**: Does osv-scanner add value over trivy for dependency scanning?

#### Key Finding: uv audit (preview) may make osv-scanner redundant

uv 0.10.10 (released 2026-03-13) includes a preview `uv audit` command that performs OSV-based dependency vulnerability scanning with batched queries and formatted reports. This is built directly into the package manager and reads from `uv.lock`.

#### Comparison

| Feature | trivy fs scan | osv-scanner | uv audit (preview) |
|---|---|---|---|
| Reads uv.lock | Yes | Yes (v2.0+) | Native |
| Vulnerability DB | NVD + vendor | OSV.dev (largest aggregated) | OSV.dev |
| Container scanning | Yes | Yes (v2.0+) | No |
| Guided remediation | No | Yes (v2.0+) | No (yet) |
| Integration effort | Low (already in stack) | Separate tool | Zero (already using uv) |

#### Recommendation: Skip osv-scanner for now

- `trivy fs .` already scans `uv.lock` for dependency vulnerabilities against NVD.
- `uv audit` (preview) will provide native OSV-based scanning with zero additional tooling.
- osv-scanner's main advantage (guided remediation, interactive HTML reports) is valuable for large dependency trees but overkill for a focused API gateway.
- Revisit if `uv audit` does not stabilize or if you need osv-scanner's remediation features.

---

## 3. Git Hooks

### 3.1 Git Hooks Manager: lefthook -- Default Confirmed

**Recommendation: lefthook**

- **Version**: v2.1.4 (released 2026-03-12)
- **Why**: Fast (Go binary), zero dependencies, parallel execution, monorepo-aware, single YAML config. Replaced husky + lint-staged for polyglot projects. Now at v2.x with signed releases.
- **Health**: HEALTHY

#### Health Check

- **Version**: v2.1.4 (released 2026-03-12) -- 6 days ago. v2.1.3 on Mar 7.
- **Activity**: 4 releases in March 2026 alone (v2.1.1 through v2.1.4). Extremely active.
- **Maintainers**: Evil Martians (mrexox / Valentin Kiselev). Backed by Evil Martians agency.
- **Stars**: 7,700 | **License**: MIT
- **CVEs**: None known
- **Notes**: v2.x introduced `setup` hook option, non-git hooks support, and auto-staging rollback. Signed releases with verified GPG keys.

---

## 4. Dev Tool Management

### 4.1 mise -- Default Confirmed (Locked In)

**Recommendation: mise**

- **Version**: 2026.3.6+ (CalVer, released March 2026)
- **Why**: Single tool replacing nvm, pyenv, asdf, direnv. Manages Python versions, Terraform, security tools, environment variables, and project tasks. Written in Rust.
- **Health**: HEALTHY

#### Health Check

- **Version**: 2026.3.6+ (CalVer)
- **Activity**: Continuous releases (multiple per week). Very active development.
- **Maintainers**: jdx (Jeff Dickey). Single primary maintainer but very prolific.
- **Stars**: 15,000+ | **License**: MIT
- **CVEs**: None known
- **Notes**: March 2026 blog post highlighted 10 underused features: task sources/outputs, secret management, daemon manager, and smart git hook integration. mise can manage all the security tools (trivy, checkov, hadolint, semgrep, gitleaks, lefthook) via its plugin/aqua backend.

---

## 5. Domain-Specific Artifacts

### 5.1 mise.toml

```toml
# mise.toml -- AI Gateway project configuration
# Manages all tool versions, env vars, and project tasks

[tools]
python = "3.13"
"pipx:checkov" = "latest"

# Security tools via aqua backend
"aqua:aquasecurity/trivy" = "0.69.2"
"aqua:hadolint/hadolint" = "2.14.0"
"aqua:evilmartians/lefthook" = "2.1.4"
"aqua:zricethezav/gitleaks" = "latest"
"aqua:anchore/grype" = "latest"  # optional, if added later

# IaC
"aqua:hashicorp/terraform" = "1.10"

[env]
_.python.venv = { path = ".venv", create = true }
PYTHONDONTWRITEBYTECODE = "1"
PYTHONUNBUFFERED = "1"

[tasks.install]
description = "Install all project dependencies"
run = "uv sync"

[tasks.dev]
description = "Run the API gateway in development mode"
run = "uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

[tasks.test]
description = "Run test suite"
run = "uv run pytest tests/ -v"
sources = ["src/**/*.py", "tests/**/*.py"]

[tasks.lint]
description = "Run linter and formatter check"
run = ["uv run ruff check .", "uv run ruff format --check ."]

[tasks.format]
description = "Auto-format code"
run = "uv run ruff format ."

[tasks.typecheck]
description = "Run type checker"
run = "uv run pyright src/"

[tasks.security]
description = "Run all security scans"
depends = ["security:sast", "security:secrets", "security:iac", "security:dockerfile"]

[tasks."security:sast"]
description = "SAST scan with semgrep"
run = "uvx semgrep scan --config p/python --config p/owasp-top-ten --config p/security-audit ."

[tasks."security:secrets"]
description = "Secret detection with gitleaks"
run = "gitleaks detect --source . --verbose"

[tasks."security:iac"]
description = "IaC security scan with checkov"
run = "uvx checkov -d infrastructure/ --framework terraform --compact"

[tasks."security:dockerfile"]
description = "Lint Dockerfiles with hadolint"
run = "hadolint Dockerfile"

[tasks."security:image"]
description = "Scan container image with trivy"
run = "trivy image --severity HIGH,CRITICAL --exit-code 1 ai-gateway:latest"

[tasks.ci]
description = "Full CI pipeline (lint, typecheck, test, security)"
depends = ["lint", "typecheck", "test", "security"]
```

### 5.2 ruff.toml

```toml
# ruff.toml -- Ruff linter and formatter configuration

target-version = "py313"
line-length = 120

[lint]
select = [
    "E",      # pycodestyle errors
    "W",      # pycodestyle warnings
    "F",      # pyflakes
    "I",      # isort
    "N",      # pep8-naming
    "UP",     # pyupgrade
    "S",      # flake8-bandit (security)
    "B",      # flake8-bugbear
    "A",      # flake8-builtins
    "C4",     # flake8-comprehensions
    "DTZ",    # flake8-datetimez
    "T10",    # flake8-debugger
    "EM",     # flake8-errmsg
    "LOG",    # flake8-logging
    "G",      # flake8-logging-format
    "PIE",    # flake8-pie
    "PT",     # flake8-pytest-style
    "RET",    # flake8-return
    "SIM",    # flake8-simplify
    "TCH",    # flake8-type-checking
    "ARG",    # flake8-unused-arguments
    "PTH",    # flake8-use-pathlib
    "ERA",    # eradicate (commented-out code)
    "PL",     # pylint
    "TRY",    # tryceratops
    "FLY",    # flynt
    "PERF",   # perflint
    "FURB",   # refurb
    "RUF",    # ruff-specific rules
]
ignore = [
    "S101",   # allow assert in tests
    "TRY003", # allow long exception messages
    "EM101",  # allow string literals in exceptions
    "EM102",  # allow f-string literals in exceptions
]

[lint.per-file-ignores]
"tests/**/*.py" = ["S101", "ARG", "PLR2004"]

[lint.isort]
known-first-party = ["app"]

[format]
quote-style = "double"
indent-style = "space"
docstring-code-format = true
```

### 5.3 pyrightconfig.json

```json
{
    "include": ["src"],
    "exclude": ["**/__pycache__", "tests"],
    "pythonVersion": "3.13",
    "pythonPlatform": "Linux",
    "typeCheckingMode": "standard",
    "reportMissingImports": true,
    "reportMissingTypeStubs": false,
    "reportUnusedImport": true,
    "reportUnusedVariable": true,
    "reportUnusedExpression": true,
    "venvPath": ".",
    "venv": ".venv"
}
```

### 5.4 lefthook.yml

```yaml
# lefthook.yml -- Git hooks configuration
# Runs checks in parallel for speed

pre-commit:
  parallel: true
  commands:
    lint:
      glob: "*.py"
      run: uv run ruff check --fix {staged_files}
      stage_fixed: true
    format:
      glob: "*.py"
      run: uv run ruff format {staged_files}
      stage_fixed: true
    typecheck:
      glob: "*.py"
      run: uv run pyright src/
    secrets:
      run: gitleaks protect --staged --verbose
    hadolint:
      glob: "Dockerfile*"
      run: hadolint {staged_files}

pre-push:
  parallel: true
  commands:
    test:
      run: uv run pytest tests/ -x -q
    sast:
      run: uvx semgrep scan --config p/python --config p/owasp-top-ten --quiet .
    iac:
      glob: "infrastructure/**/*.tf"
      run: uvx checkov -d infrastructure/ --framework terraform --compact --quiet
```

### 5.5 .hadolint.yaml

```yaml
# .hadolint.yaml -- Hadolint configuration
ignored:
  - DL3008  # Pin versions in apt-get install (handled by base image choice)
trustedRegistries:
  - public.ecr.aws
  - cgr.dev  # Chainguard
  - gcr.io/distroless
```

### 5.6 .semgrepconfig.yml

```yaml
# .semgrepconfig.yml -- Semgrep project configuration
rules:
  - p/python
  - p/owasp-top-ten
  - p/security-audit
  - p/dockerfile
exclude:
  - tests/
  - "*.test.py"
  - infrastructure/
```

### 5.7 .gitleaks.toml

```toml
# .gitleaks.toml -- Gitleaks configuration
title = "AI Gateway Secret Detection"

[allowlist]
description = "Global allowlist"
paths = [
    '''\.venv/''',
    '''node_modules/''',
    '''\.git/''',
    '''tests/fixtures/''',
]
```

### 5.8 .editorconfig

```ini
# .editorconfig -- Consistent editor settings
root = true

[*]
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
charset = utf-8

[*.py]
indent_style = space
indent_size = 4
max_line_length = 120

[*.{toml,yaml,yml}]
indent_style = space
indent_size = 2

[*.tf]
indent_style = space
indent_size = 2

[Dockerfile*]
indent_style = space
indent_size = 4

[Makefile]
indent_style = tab
```

---

## 6. Developer Workflow

### 6.1 Onboarding (New Developer Setup)

```bash
# 1. Install mise (one-time)
curl https://mise.run | sh
echo 'eval "$(mise activate zsh)"' >> ~/.zshrc
source ~/.zshrc

# 2. Clone the repo
git clone <repo-url> && cd ai-gateway

# 3. Install all tools (mise reads mise.toml automatically)
mise install

# 4. Install git hooks
lefthook install

# 5. Install Python dependencies
uv sync

# 6. Verify everything works
mise run ci
```

Total onboarding time: under 5 minutes.

### 6.2 Daily Development Loop

```
Edit code
  |
  v
Save -> ruff auto-formats (editor integration)
  |
  v
git add -> lefthook pre-commit fires:
  - ruff check + format (parallel)
  - pyright typecheck (parallel)
  - gitleaks protect (parallel)
  - hadolint if Dockerfile changed (parallel)
  |
  v
git push -> lefthook pre-push fires:
  - pytest (fast, -x flag exits on first failure)
  - semgrep SAST scan
  - checkov IaC scan (if .tf files changed)
```

### 6.3 CI/CD Integration Points

| Stage | Tool | Trigger | Exit Code |
|---|---|---|---|
| Lint | ruff check + format --check | Every PR | Fail on violations |
| Type Check | pyright | Every PR | Fail on errors |
| Unit Tests | pytest | Every PR | Fail on failures |
| SAST | semgrep | Every PR | Fail on HIGH+ findings |
| Secrets | gitleaks | Every PR | Fail on any detection |
| IaC Security | checkov | PR touching infrastructure/ | Fail on FAILED checks |
| Dockerfile Lint | hadolint | PR touching Dockerfile | Fail on error-level rules |
| Image Scan | trivy image | Post-build, pre-deploy | Fail on HIGH+CRITICAL |
| Dep Vulnerabilities | trivy fs / uv audit | Weekly scheduled + PR | Fail on CRITICAL |

---

## 7. Compatibility Notes

### Tool Interoperability

1. **ruff + pyright**: Fully compatible. ruff handles formatting and linting, pyright handles type checking. No overlap or conflict. Both support Python 3.13.

2. **uv + mise**: Complementary. mise manages the Python version, uv manages packages and the venv. Use `mise sync python --uv` to keep them aligned. The `_.python.venv` setting in mise.toml auto-creates the venv that uv uses.

3. **checkov + trivy**: Overlapping IaC coverage (both scan Terraform). Recommendation: use checkov for IaC (deeper policy library, 2,500+ checks) and trivy for container images + filesystem scanning. Avoid running both on the same Terraform files in CI to prevent duplicate findings.

4. **semgrep + ruff**: Minimal overlap. ruff's `S` (bandit) rules cover basic security patterns. semgrep goes deeper with taint tracking and OWASP rules. Keep both -- ruff catches simple issues fast in pre-commit, semgrep catches complex issues in pre-push/CI.

5. **gitleaks + semgrep**: semgrep has some secret detection rules, but gitleaks is purpose-built with 160+ detectors. Keep both -- different strengths.

6. **hadolint + trivy misconfig + checkov**: All three can lint Dockerfiles. hadolint is the most thorough for Dockerfile-specific best practices (ShellCheck integration). trivy and checkov catch security misconfigurations. In practice, hadolint in pre-commit catches developer mistakes; trivy/checkov in CI catch security policy violations.

7. **trivy incident note**: The March 2026 supply chain incident (CVE-2026-28353) affected the GitHub repo and VSCode extension, not the CLI binary or scanning engine. Pin versions and verify checksums in CI.

### License Compatibility

All recommended tools use permissive licenses compatible with an internal API service:

| Tool | License |
|---|---|
| ruff | MIT |
| pyright | MIT |
| uv | MIT OR Apache-2.0 |
| pytest | MIT |
| checkov | Apache 2.0 |
| trivy | Apache 2.0 |
| hadolint | GPL-3.0 (CLI tool, no linking) |
| semgrep CE | LGPL-2.1 (CLI tool, no linking) |
| gitleaks | MIT |
| lefthook | MIT |
| mise | MIT |

hadolint's GPL-3.0 and semgrep's LGPL-2.1 are fine because they are standalone CLI tools. You are not linking against or distributing them as part of your application.

---

## 8. Sources

1. Ruff v0.15.6 release notes -- https://github.com/astral-sh/ruff/releases
2. Ruff v0.15.0 blog post (2026 style guide) -- https://astral.sh/blog/ruff-v0.15.0
3. Pyright releases (Microsoft) -- https://github.com/microsoft/pyright/releases
4. Trivy vs Grype scan comparison -- https://www.linkedin.com/pulse/vulnerability-scan-results-grype-vs-trivy-real-docker-amr-amin-shw9f
5. Open-Source SCA Tools Compared (2026) -- https://appsecsanta.com/sca-tools/open-source-sca-tools
6. Grype documentation -- https://oss.anchore.com/docs/reference/grype/cli/
7. Trivy security incident report -- https://github.com/aquasecurity/trivy/discussions/10265
8. Trivy incident coverage -- https://awesomeagents.ai/news/hackerbot-claw-trivy-github-actions-compromise/
9. Checkov changelog -- https://github.com/bridgecrewio/checkov/blob/main/CHANGELOG.md
10. Checkov releases -- https://github.com/bridgecrewio/checkov/releases
11. Hadolint releases -- https://github.com/hadolint/hadolint/releases
12. Hadolint maintenance issue -- https://github.com/hadolint/hadolint/issues/1110
13. Semgrep February 2026 release notes -- https://semgrep.dev/docs/release-notes/february-2026
14. Betterleaks announcement -- https://www.aikido.dev/blog/betterleaks-gitleaks-successor
15. Betterleaks launch coverage -- https://www.bleepingcomputer.com/news/security/betterleaks-a-new-open-source-secrets-scanner-to-replace-gitleaks/
16. uv releases -- https://github.com/astral-sh/uv/releases
17. uv 0.10.10 release (uv audit preview) -- https://newreleases.io/project/github/astral-sh/uv/release/0.10.10
18. Lefthook releases -- https://github.com/evilmartians/lefthook/releases
19. Lefthook v2.1.4 -- https://newreleases.io/project/github/evilmartians/lefthook/release/v2.1.4
20. OSV-Scanner vs Trivy discussion -- https://github.com/google/osv-scanner/issues/2330
21. OSV-Scanner review -- https://appsecsanta.com/osv-scanner
22. pytest 9.0 overview -- https://kapildagur.medium.com/pytest-the-python-testing-framework-that-actually-makes-you-want-to-write-tests-1c0ea4bbe464
23. mise features blog -- https://jdx.dev/posts/2026-03-02-10-mise-features/
24. Trivy 2026 overview -- https://appsecsanta.com/trivy
25. Docker Container Security Scanning 2026 -- https://zeonedge.com/en/blog/docker-container-security-scanning-trivy-grype-2026
26. Container scanning comparison -- https://aquilax.ai/blog/scan-docker-images-vulnerabilities
27. CVE-2026-28353 (Trivy) Reddit discussion -- https://www.reddit.com/r/devops/comments/1rqmrhi/
28. Hadolint mise versions -- https://mise-tools.jdx.dev/tools/hadolint

---

## Quality Checklist

- [x] Every recommendation has a health check
- [x] RESEARCH categories (grype overlap, osv-scanner overlap) have comparison matrices
- [x] Opinionated defaults confirmed with live version data, not assumed
- [x] Dependency versions are current (verified March 2026)
- [x] Sources cited for all factual claims
- [x] Recommendations are coherent (tools work well together)
- [x] User constraints respected: must-haves (trivy, checkov, hadolint) included; locked-in (Python 3.13, uv) validated; avoid list (Kubernetes, LiteLLM) not referenced
- [x] Config snippets syntactically valid
- [x] Only Python-relevant tools included (JS/TS tooling skipped)