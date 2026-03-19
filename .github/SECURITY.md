# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email security concerns to the repository owner. You can expect an initial response within 48 hours.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| main    | Yes       |

## Security Scanning

This project runs automated security scans on every commit:

- **SAST**: Semgrep with Python, OWASP Top 10, and security audit rulesets
- **Secrets**: Gitleaks for credential detection
- **IaC**: Checkov for Terraform misconfigurations
- **Container**: Trivy for vulnerability scanning, Hadolint for Dockerfile linting
- **Supply chain**: Cosign image signing, Syft SBOM generation, Dependency Review
- **Code analysis**: GitHub CodeQL
- **Scorecard**: OpenSSF Scorecard for supply chain security posture
