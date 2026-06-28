# Verification status + how to finish on a dev box

## What was verified in the authoring sandbox

- **rustfmt `--check` passes** on all three edited files against the repo's `rustfmt.toml` (hard tabs, 2-space, match trailing comma). Exit 0.
- Change is **source-grounded**: every symbol used (`GuardrailOutcome::None`, `GuardrailSource::{Input,Output}` which is `pub` + `Copy`, `is_blocked`/`is_anonymized`, the `BedrockGuardrails` struct, the `#[apply(schema!)]` macro) was read from the actual files before use.
- Commit is **DCO-signed** (`Signed-off-by: Laith Al-Saadoon`), required by the project CHARTER.

## What could NOT be verified here — and why

A full `cargo check` / `clippy` / `test` was **not run**. The sandbox has **no `protoc`** (the `crates/protos` build needs it) and `cargo metadata` times out on the cold workspace. So the patch is **not compile-verified**. Treat it as a high-confidence draft, not a green build.

## To finish (on a machine with the Rust 1.96 toolchain + protoc)

```bash
git checkout feature/bedrock-guardrails-detect-only
make lint                          # cargo fmt --check + clippy -D warnings
make generate-schema               # REQUIRED: regenerate schema/config.json for the new detectOnly field
git add schema/ && git commit -s -m "gen: regenerate schema for detectOnly"
make generate-schema check-clean-repo   # must report a clean tree
make test                          # cargo test (insta snapshots); if a snapshot needs updating, cargo insta review
```

Only after `make lint`, `check-clean-repo`, and `make test` are green should the PR be opened.

## Likely compile nits to watch (low risk, but where they'd surface)

- `serde_json::to_string(&self.assessments)` — `assessments` is `Vec<serde_json::Value>`, which is `Serialize`, so this is fine. If clippy flags the `.unwrap_or_default()`, it's cosmetic.
- The `#[apply(schema!)]` macro on `BedrockGuardrails` must accept the new `bool` field with `#[serde(default, rename = "detectOnly")]` — the same attribute pattern is already used on sibling fields in that file, so it should expand cleanly.
- `would_action()` borrows `&self` and returns `&'static str` — no lifetime issue.

## Wiring our pinned image to the fork (until upstream merges)

Our data-plane image pins the upstream agentgateway image by digest (`versions.env` `AGENTGATEWAY_IMAGE_DIGEST`). To run detect-only *before* upstream merges, build from the fork instead of re-tagging the upstream image:

1. Push `feature/bedrock-guardrails-detect-only` to our fork of agentgateway.
2. Temporarily change the ai-gateway `Dockerfile` from the re-tag form to a from-source build of the fork (multi-stage `rust:1.96` builder → distroless runner), or build the fork image in CI and set `AGENTGATEWAY_IMAGE` to that.
3. Once upstream merges and cuts a release, revert to the digest-pin of the official image and drop the fork build.

This keeps the fork as a temporary bridge, not a permanent maintenance burden — the whole point of upstreaming.
