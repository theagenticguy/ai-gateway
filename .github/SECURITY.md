# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Please use [GitHub Security Advisories](https://github.com/theagenticguy/ai-gateway/security/advisories/new) to report vulnerabilities privately. This ensures the issue is triaged confidentially before any public disclosure.

Alternatively, you can email security concerns to the repository owner. You can expect:

- **Initial response**: Within 48 hours
- **Status update**: Within 5 business days
- **Resolution target**: Within 30 days for critical issues, 90 days for others

## Supported Versions

| Version | Supported |
| ------- | --------- |
| main    | Yes       |

## Disclosure Policy

We follow [coordinated vulnerability disclosure](https://cheatsheetseries.owasp.org/cheatsheets/Vulnerability_Disclosure_Cheat_Sheet.html). After a fix is available, we will:

1. Release the fix to `main`
2. Publish a GitHub Security Advisory
3. Credit the reporter (unless they prefer anonymity)

## Security Scanning

This project runs automated security scans on every push and pull request:

| Layer | Tool | What It Covers |
| ----- | ---- | -------------- |
| SAST | [Semgrep](https://semgrep.dev/) | Python code analysis via container (OWASP Top 10, security audit) |
| SAST | [Bandit](https://bandit.readthedocs.io/) | Python-specific security linter with SARIF upload |
| SAST | [CodeQL](https://codeql.github.com/) | GitHub-native semantic analysis (security-extended + quality) |
| Secrets | [Gitleaks](https://gitleaks.io/) | Prevents secrets from entering the repository (SARIF upload) |
| IaC | [Checkov](https://www.checkov.io/) | Terraform security and compliance (2,500+ policies) |
| IaC | [TFLint](https://github.com/terraform-linters/tflint) | Terraform linting with AWS ruleset |
| Dockerfile | [Hadolint](https://github.com/hadolint/hadolint) | Dockerfile best practices with ShellCheck integration |
| Container | [Trivy](https://trivy.dev/) | Vulnerability scanning of container images (HIGH + CRITICAL) |
| Filesystem | [Trivy](https://trivy.dev/) | Repository filesystem scan for misconfigurations |
| Dependencies | [pip-audit](https://github.com/pypa/pip-audit) | Python dependency vulnerability audit |
| Dependencies | [OSV-Scanner](https://github.com/google/osv-scanner) | Lockfile scanning (uv.lock + pnpm-lock.yaml in CI; `mise run security:osv` scans every lockfile in the tree recursively, including clients/admin_cli/uv.lock) |
| Dependencies | [Dependency Review](https://github.com/actions/dependency-review-action) | PR-time vulnerability and license check |
| Dependencies | [Dependabot](https://docs.github.com/en/code-security/dependabot) | Automated updates for Python, npm, Terraform, Actions, Docker |
| Licenses | `scripts/check-licenses.py` | Enforcing license policy: SPDX-normalized allowlist, copyleft denied by family, evidenced exemptions only (fails the `licenses` CI job and the pre-push hook) |
| Licenses | [pip-licenses](https://github.com/raimon49/pip-licenses) | License compliance reporting (JSON + Markdown artifacts) |
| License headers | `scripts/check-license-headers.py` | Every tracked .py/.sh/.tf file carries `SPDX-License-Identifier: Apache-2.0` (pre-commit hook + `supply-chain` CI job) |
| SBOM | [Syft](https://github.com/anchore/syft) | CycloneDX + SPDX software bill of materials generation: a source-tree SBOM in the `supply-chain` CI job (uploaded as the `sbom-source` artifact, 90-day retention) and an image SBOM in the container job and release workflow |
| SBOM Scan | [Grype](https://github.com/anchore/grype) | Scans the source SBOM on every push/PR in the `supply-chain` job; fail threshold and accepted findings live in `.grype.yaml` |
| Workflow lint | [actionlint](https://github.com/rhysd/actionlint) | GitHub Actions workflow linting with ShellCheck (`workflow-lint` CI job + pre-commit hook) |
| Signing | [Cosign](https://github.com/sigstore/cosign) | Keyless image signing via Sigstore OIDC |
| Supply chain | [OpenSSF Scorecard](https://scorecard.dev/) | Supply chain security posture assessment |
| SBOM Rescan | [Grype](https://github.com/anchore/grype) | Nightly SBOM re-scan against updated vulnerability databases |
| ECR Scanning | [Amazon Inspector](https://aws.amazon.com/inspector/) | Continuous container image scanning (re-evaluates on new CVEs) |
| Provenance | [GitHub Attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations) | SLSA build provenance for container images |

All GitHub Actions are pinned to SHA hashes to prevent supply chain attacks.
Container base images used in CI workflows are pinned by digest to prevent tag repointing attacks.

## Dependency Management

- Dependencies are managed via `uv` with a locked `uv.lock` file
- Terraform providers are version-constrained in `versions.tf` and all child modules
- Dependabot monitors 5 ecosystems (Python, npm, Terraform, Actions, Docker) weekly
- The Dependency Review action blocks PRs introducing HIGH+ severity vulnerabilities or GPL-3.0/AGPL-3.0 licenses
- The license policy gate (`scripts/check-licenses.py`) enforces an SPDX-normalized allowlist over all installed Python dependencies; strong and network copyleft families (GPL, AGPL, LGPL, SSPL, CDDL, EPL, CPL, OSL, EUPL) are denied, and the single exemption (atheris) carries documented evidence
- OSV-Scanner checks `uv.lock` and `docs/pnpm-lock.yaml` in CI; the local `mise run security:osv` task discovers and scans every lockfile recursively, so new lockfiles are covered without editing the task
- Grype findings accepted as false positives are recorded in `.grype.yaml`, each with the reason and the date it was checked
