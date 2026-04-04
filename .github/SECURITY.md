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
| SAST | [Semgrep](https://semgrep.dev/) | Python code analysis (OWASP Top 10, security audit rules) |
| Secrets | [Gitleaks](https://gitleaks.io/) | Prevents secrets from entering the repository |
| IaC | [Checkov](https://www.checkov.io/) | Terraform security and compliance (2,500+ policies) |
| Dockerfile | [Hadolint](https://github.com/hadolint/hadolint) | Dockerfile best practices with ShellCheck integration |
| Container | [Trivy](https://trivy.dev/) | Vulnerability scanning of container images (HIGH + CRITICAL) |
| SBOM | [Syft](https://github.com/anchore/syft) | CycloneDX software bill of materials generation |
| Code analysis | [CodeQL](https://codeql.github.com/) | GitHub-native semantic code analysis |
| Supply chain | [OpenSSF Scorecard](https://scorecard.dev/) | Supply chain security posture assessment |
| Dependencies | [Dependency Review](https://github.com/actions/dependency-review-action) | PR-time vulnerability and license check |
| Updates | [Dependabot](https://docs.github.com/en/code-security/dependabot) | Automated dependency updates (Python, Terraform, Actions) |
| SBOM Rescan | [Grype](https://github.com/anchore/grype) | Nightly SBOM re-scan against updated vulnerability databases |
| Lockfile Scan | [OSV-Scanner](https://github.com/google/osv-scanner) | Lockfile scanning against the OSV vulnerability database |
| ECR Scanning | [Amazon Inspector](https://aws.amazon.com/inspector/) | Continuous container image scanning (re-evaluates on new CVEs) |
| Provenance | [GitHub Attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations) | SLSA build provenance for container images |

All GitHub Actions are pinned to SHA hashes to prevent supply chain attacks.
Container base images are pinned by digest to prevent tag repointing attacks.

## Dependency Management

- Dependencies are managed via `uv` with a locked `uv.lock` file
- Terraform providers are version-constrained in `versions.tf` and all child modules
- Dependabot monitors Python (pip), Terraform, GitHub Actions, and npm (docs/) dependencies weekly
- The Dependency Review action blocks PRs introducing HIGH+ severity vulnerabilities or GPL-3.0/AGPL-3.0 licenses
