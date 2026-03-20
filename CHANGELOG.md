# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Bug Fixes

- Correct codeql-action SHA pins, add pre-flight lint to bot-push (#32) by @bonk-ai[bot] in [#32](https://github.com/theagenticguy/ai-gateway/pull/32)
- Correct scorecard-action SHA pin and add CONTRIBUTING.md by @bonk-ai[bot] in [#29](https://github.com/theagenticguy/ai-gateway/pull/29)
- Address Copilot review — guard non-dict usage and non-string provider by @theagenticguy
- Add default region to cloudwatch client for CI test env by @theagenticguy
- Skip CKV2_AWS_50 Multi-AZ failover check (single-node dev cache) by @theagenticguy
- Skip Checkov false positive on ElastiCache and soften Trivy by @theagenticguy
- Restore SARIF uploads for public repo, fix Checkov and Semgrep by @theagenticguy
- Remove SARIF uploads and disable CodeQL/Scorecard for private repo by @theagenticguy
- Wire cache_node_type and remove unused default_routing_strategy by @theagenticguy
- Add version constraints and required_version to all child modules by @theagenticguy
- Use dedicated PAT for OpenSSF Scorecard by @theagenticguy
- Add required_providers to all child modules and ignore unfixed CVEs by @theagenticguy
- Add boto3 dep for pyright and regenerate terraform-docs by @theagenticguy
- Resolve ruff lint errors and terraform fmt in B.3 cost attribution by @theagenticguy

### CI/CD

- Remove Build & Push and Deploy jobs from CI pipeline by @theagenticguy
- Add full security pipeline, README, and repo quality infrastructure by @theagenticguy

### Documentation

- Add PR template, issue templates, and repo labels by @theagenticguy
- Add Zensical documentation site with GitHub Pages deployment by @theagenticguy
- Expand routing strategies documentation and ADR-009 by @theagenticguy

### Features

- Add semver versioning policy and release bump tasks by @bonk-ai[bot] in [#30](https://github.com/theagenticguy/ai-gateway/pull/30)
- Add property-based fuzz tests for cost attribution by @theagenticguy
- Add ElastiCache Redis response cache layer by @theagenticguy
- Add Bedrock Guardrails module for content safety filtering by @theagenticguy
- Add cost attribution pipeline with Lambda + CloudWatch metrics by @theagenticguy
- Add provider fallback and load-balance configurations by @theagenticguy
- Add multi-client onboarding module for per-team Cognito credentials by @theagenticguy

### Refactoring

- Adopt Pydantic v2 models for cost attribution validation by @theagenticguy
- Extract 4 local modules + terraform-docs + structural cleanup by @theagenticguy

### Security

- Pin all actions to SHA hashes, add tests to CI, enhance SECURITY.md by @theagenticguy
- Bump all GitHub Actions + TF community modules to latest by @theagenticguy
- Remediate all checkov findings + add Terragrunt multi-env by @theagenticguy

### Testing

- Bonk-ai[bot] verified commit (no custom author) by @bonk-ai[bot] in [#22](https://github.com/theagenticguy/ai-gateway/pull/22)

