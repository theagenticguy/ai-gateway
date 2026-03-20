# Versioning Policy

AI Gateway follows [Semantic Versioning 2.0.0](https://semver.org/) with the format `MAJOR.MINOR.PATCH`.

## What Constitutes Each Bump

| Bump | When | Examples |
|------|------|----------|
| **MAJOR** | Breaking changes to the gateway API, authentication scheme, or infrastructure that require consumer action | Changing auth from Cognito M2M to a different scheme; removing or renaming API endpoints; Terraform state-breaking module restructures |
| **MINOR** | New functionality that is backward-compatible | Adding a new model provider; new API endpoints; new Terraform modules (cache, guardrails); new observability features |
| **PATCH** | Backward-compatible bug fixes, security patches, dependency updates, documentation, and CI improvements | Fixing JWT validation edge cases; updating pinned action SHAs; Portkey version bumps; Terraform variable defaults |

## Pre-release Tags

Pre-release versions use suffixes per semver spec:

- `v1.2.0-alpha.1` — Early development, unstable
- `v1.2.0-beta.1` — Feature-complete but under testing
- `v1.2.0-rc.1` — Release candidate

The release workflow automatically marks these as pre-release on GitHub.

## Version Sources

The canonical version lives in two places that must stay in sync:

| File | Field | Purpose |
|------|-------|---------|
| `pyproject.toml` | `version` | Python package version |
| Git tag | `v{version}` | Triggers the release workflow |

Use `mise run release:bump-*` tasks to update both atomically.

## Changelog

`CHANGELOG.md` is automatically generated from conventional commit messages using [git-cliff](https://git-cliff.org/). Configuration lives in `cliff.toml`.

- Preview unreleased changes: `mise run release:changelog`
- Full regeneration happens automatically during version bumps

Commits are grouped by type (Features, Bug Fixes, Security, etc.) with PR links and author attribution.

## Release Flow

1. Work is merged to `main` via PRs (using conventional commit messages)
2. When ready to release, run the appropriate bump task:
   ```bash
   mise run release:bump-patch   # 0.1.0 → 0.1.1
   mise run release:bump-minor   # 0.1.0 → 0.2.0
   mise run release:bump-major   # 0.1.0 → 1.0.0
   ```
3. The task updates `pyproject.toml`, generates `CHANGELOG.md`, commits, and tags
4. Push the tag: `git push origin main --tags`
5. The `v*` tag triggers `.github/workflows/release.yml` which:
   - Builds and pushes the container image to ECR
   - Signs the image with cosign (keyless)
   - Generates CycloneDX and SPDX SBOMs
   - Creates a GitHub Release with auto-generated changelog

## Current Status

The project is in initial development (`0.x.y`). Per semver, the public API is not yet considered stable. Minor versions may include breaking changes until `1.0.0`.
