# Verification status

## COMPILE-VERIFIED (2026-06-28)

After installing `protobuf` (protoc 35.1) + `cmake` via Homebrew on the host, the
full toolchain became available and the patch was verified on **Rust 1.96 +
protoc 35.1**. All green:

- `cargo check -p agentgateway` — clean (the build caught two xDS construction
  sites in `agent_xds.rs` that the first commit missed; both fixed).
- `cargo clippy -p agentgateway` — clean with `-D warnings`.
- `cargo test -p agentgateway --lib llm::policy` — **83 passed, 0 failed**,
  including the three new `would_action` tests.
- `cargo fmt --all -- --check` — clean (full workspace, the real lint gate).
- `make generate-schema` — regenerated `schema/config.json` + `schema/config.md`
  with `detectOnly` on every guardrail attachment path.
- Go controller: `go build` + `go vet` of `./api/...` and
  `./pkg/agentgateway/plugins/...` — clean after regenerating the Go proto
  bindings (`buf generate`) and CRD Helm templates.

The feature is threaded **end to end**: config-file path (Rust) AND Kubernetes
CRD → controller → xDS → Rust. Two commits on the branch:
`7dae776` (config-file path + behavior) and `bd81ddb` (xDS/CRD/proto/schema +
the compiler-caught fixes).

Commits are **DCO-signed** (`Signed-off-by: Laith Al-Saadoon`).

## Full `make test` — RUN, green (2026-06-28)

`cargo test --all-targets` (== `make test`) on Rust 1.96: **1242 passed in the
main lib bin, 0 failed**, plus all other test bins 0-failed. No snapshot changes
(the new field defaults false, so existing insta snapshots are unaffected).

One environmental note (not a code issue): `http::auth::tests::test_aws_sign_request_no_region_error`
fails on this host because `~/.aws/config` sets `region = us-east-1`, which the
AWS SDK region-provider chain reads even with `AWS_REGION` unset, so the test's
"no region → error" assertion doesn't hold. It is **unrelated to this change**
(the branch does not touch `http/auth`) and **passes in a clean env**, matching
CI:

```bash
env -u AWS_BEARER_TOKEN_BEDROCK AWS_CONFIG_FILE=/dev/null \
    AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true \
    cargo +1.96 test --all-targets    # fully green
```

The keycloak example-validation tests (`tests/validate_examples.rs`) skip
cleanly without `KEYCLOAK_AVAILABLE=1` (CI only sets it on blacksmith runners),
so they need no docker here and are unaffected by this change.

## Remaining before opening the PR

- `make generate-apis check-clean-repo` to confirm the committed generated files
  match a fresh regen (they were generated here, so this should be a no-op).
- Push the fork branch and open the issue/PR (awaiting go-ahead).

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
