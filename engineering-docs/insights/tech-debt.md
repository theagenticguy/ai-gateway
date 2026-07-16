# ai-gateway · Tech debt

This register was assembled from four evidence sources, in order of objectivity: (1) explicit comment markers grepped across the tree (`\bTODO\b`, `FIXME`, `HACK`, `XXX`, `REFACTOR`, `WORKAROUND`, `DEPRECATED`, plus the repo's own `[VERIFY]` and `# NOTE:` conventions), cross-checked against `.analysis/.repomix/codebase.json`; (2) version-pinning constraints in `pyproject.toml` and `docs/package.json`; (3) suppressed static-analysis findings (the checkov `skip_check` list in CI); and (4) pattern-level smells confirmed by reading representative sites — duplicated logic, error-swallowing, near-identical CRUD handlers, hand-maintained tables, and a duplicated doc tree. Category names use a closed vocabulary (`marker` / `wrong abstraction` / `error handling` / `dead code adjacent` / `deprecated pattern` / `version pin` / `duplicated logic` / `missing tests`); cost-to-fix is `S` / `M` / `L`. Rank reflects `cost-to-fix × consequence-of-leaving`, so high-consequence-but-cheap items sort above expensive cosmetic ones.

Context worth stating up front: this is a clean, disciplined Python 3.13 codebase. It carries no `FIXME`/`HACK`/`XXX`/`WORKAROUND` markers, no `@deprecated` decorators, and no skipped or `xfail` tests; every `src/<module>` has a colocated `tests/test_<module>.py`. The recent data-plane migration off Portkey OSS to agentgateway (ADR-017) was cut cleanly — no Portkey shims survive in `src/`, `tests/`, `clients/`, or `infrastructure/`; residual Portkey mentions live only in ADRs, docs, and spike annotations. The debt below is therefore concentrated in a handful of duplication seams, suppressed IaC findings, undeployed-but-shipped code, and hand-maintained lookup tables — not in rot.

## Ranked register

| Rank | Debt item | Category | Cost to fix | Citation |
|---|---|---|---|---|
| 1 | Six checkov IaC checks suppressed in CI (`CKV_TF_1, CKV2_AWS_50, CKV_AWS_115, CKV_AWS_116, CKV_AWS_117, CKV_AWS_272`) that are absent from `.checkov.yml` and carry no in-repo justification, unlike the 22 documented shared skips | `deprecated pattern` | M | `.github/workflows/ci.yml:341` |
| 2 | Unverified JWT base64-decode logic hand-copied three times, only one typed against specific exceptions | `duplicated logic` | M | `src/gwcore/auth.py:89`, `src/cost_attribution/handler.py:63`, `src/budget_enforcement/jwt_utils.py:26` |
| 3 | Silent error-swallow: identity resolution returns `("unknown","unknown")` on any exception with no log line, unlike its logging siblings — masks JWT-shape regressions in cost attribution | `error handling` | S | `src/cost_attribution/handler.py:119` |
| 4 | Three lambdas (`usage_api`, `pricing_admin`, `rate_limiter`) ship full handlers + tests but have no `aws_lambda_function` Terraform resource — code exists, deployment does not (issue #55) | `marker` | L | `src/usage_api/__init__.py:1`, `src/pricing_admin/__init__.py:1`, `src/rate_limiter/__init__.py:1` |
| 5 | Near-identical CRUD handler dispatch + authz-deny-audit + `except Exception` epilogue duplicated across three admin lambdas, each with its own `# noqa: PLR0911` | `duplicated logic` | M | `src/pricing_admin/handler.py:245`, `src/routing_config/handler.py:269`, `src/budget_admin/handler.py:164` |
| 6 | ADR-001 and ADR-006 still marked **Accepted** though ADR-017 reversed the Portkey engine choice they justify ("The engine choice in ADR-001 aged") — only ADR-012 was re-statused to Superseded | `deprecated pattern` | S | `adr/001-portkey-oss-over-litellm.md:3`, `adr/006-portkey-dual-format-api.md:3`, `adr/017-agentgateway-data-plane-spike.md:39` |
| 7 | Hand-maintained per-model pricing fallback table; new model IDs must be added by editing source or the DynamoDB overlay or requests trip `UnknownModelPrice` | `wrong abstraction` | M | `src/cost_attribution/pricing.py:80` |
| 8 | ADR set duplicated across two trees (`adr/*.md` and `docs/src/content/docs/adrs/*.md`, identical 17-ADR set) that must be kept in sync by hand | `duplicated logic` | M | `adr/012-response-cache-strategy.md:3`, `docs/src/content/docs/adrs/012-response-cache-strategy.md:12` |
| 9 | Eight `[VERIFY]` markers in the agentgateway spike config — inferred-not-confirmed fields (ports, auth-ref syntax, webhook path field, CEL access-log block) carried into the migration reference | `marker` | M | `spikes/agentgateway-data-plane/config.yaml:20`, `spikes/agentgateway-data-plane/config.yaml:56`, `spikes/agentgateway-data-plane/docker-compose.yaml:14` |
| 10 | Two observability widgets/queries deleted-in-place with explanatory NOTEs because `TimeToFirstToken` is emitted by nothing — dashboards permanently miss TTFT until the data plane emits the field | `dead code adjacent` | M | `infrastructure/modules/observability/main.tf:312`, `infrastructure/modules/observability/saved_queries.tf:85` |
| 11 | `pyproject.toml` `exclude-newer = "7 days"` freshness cap silently holds back any dependency newer than the window; invisible pin that ages unless consciously bumped | `version pin` | S | `pyproject.toml:27` |
| 12 | `pygments>=2.20.0` constraint carried for CVE-2026-4539 (ReDoS in AdlLexer) — a manual security floor to drop once the transitive dep resolves it natively | `version pin` | S | `pyproject.toml:29` |
| 13 | Three transitive-CVE pnpm `overrides` (`lodash-es`, `esbuild`, `dompurify`) in the docs site — hand-forced minimums that must be revisited on each `astro` bump | `version pin` | S | `docs/package.json:31` |
| 14 | Open TODO carried into the migration reference: header forwarding (`x-amzn-oidc-data`) behavior is "becoming configurable" upstream and unconfirmed against the pinned agentgateway version | `marker` | S | `spikes/agentgateway-data-plane/webhook-adapter.md:127` |

## Explicit markers

The repo uses a narrow marker vocabulary: `TODO` for tracked-but-undone work, `# NOTE:` for deliberate deletions/context, and `[VERIFY]` for spike fields inferred from upstream source rather than confirmed. No `FIXME`, `HACK`, `XXX`, `WORKAROUND`, or `@deprecated` markers exist. Every marker is quoted verbatim below.

- `# TODO: Not yet deployed — no aws_lambda_function Terraform resource exists.` — `src/usage_api/__init__.py:1`
- `# TODO: Not yet deployed — no aws_lambda_function Terraform resource exists.` — `src/pricing_admin/__init__.py:1`
- `# TODO: Not yet deployed — no aws_lambda_function Terraform resource exists.` — `src/rate_limiter/__init__.py:1`
- `- Confirm header forwarding includes \`x-amzn-oidc-data\` by default or needs \`forward_header_matches\` (\`webhook.rs:160-167\` has a TODO that header forwarding is becoming configurable).` — `spikes/agentgateway-data-plane/webhook-adapter.md:127`
- `# NOTE: agentgateway's local-config surface is evolving. Fields marked [VERIFY]` — `spikes/agentgateway-data-plane/config.yaml:14`
- `# NOTE: the Time-to-First-Token widget was removed. \`TimeToFirstToken\` is` — `infrastructure/modules/observability/main.tf:312`
- `# NOTE: the ttft-percentiles saved query was removed. \`timeToFirstToken\` is not` — `infrastructure/modules/observability/saved_queries.tf:85`
- `# In ECS, set this to 8787 so the ALB target group is unchanged. [VERIFY port override]` — `spikes/agentgateway-data-plane/config.yaml:20`
- `# Portkey today. [VERIFY env-var auth ref syntax]` — `spikes/agentgateway-data-plane/config.yaml:56`
- `path: /budget/request   # [VERIFY path field name]` — `spikes/agentgateway-data-plane/config.yaml:83`
- `path: /scan/request     # [VERIFY path field name]` — `spikes/agentgateway-data-plane/config.yaml:93`
- `# log needs a CEL-shaped record that re-keys them. [VERIFY access-log CEL syntax]` — `spikes/agentgateway-data-plane/config.yaml:118`
- `# config:                                    # [VERIFY top-level telemetry block]` — `spikes/agentgateway-data-plane/config.yaml:120`
- `# (agentgateway/Dockerfile:95,106). [VERIFY published image ref + tag]` — `spikes/agentgateway-data-plane/docker-compose.yaml:12`
- `command: ["-f", "/config/config.yaml"]   # [VERIFY flag: -f / --file]` — `spikes/agentgateway-data-plane/docker-compose.yaml:14`
- `- "15000:15000"   # admin/metrics, if exposed [VERIFY admin port]` — `spikes/agentgateway-data-plane/docker-compose.yaml:17`

## Pattern-level smells

### Copy-pasted unverified JWT decode

Three separate functions decode a JWT payload by hand — split on `.`, re-pad base64, `urlsafe_b64decode`, `json.loads` — to read Cognito claims without signature verification (safe only because the ALB already verified upstream). The canonical `gwcore.auth.decode_claims` narrows to `(binascii.Error, ValueError, UnicodeDecodeError)`, but the two lambda-local copies catch a bare `except Exception`, so any bug in decode is indistinguishable from a malformed token. `budget_enforcement` and `cost_attribution` both import `gwcore` already, so consolidating onto `decode_claims` is a mechanical change with no new dependency.

Shows up in:
- `src/gwcore/auth.py:89`
- `src/cost_attribution/handler.py:63`
- `src/budget_enforcement/jwt_utils.py:26`

Cost: M

### Near-identical CRUD lambda handlers

The admin lambdas (`pricing_admin`, `routing_config`, `budget_admin`) share a `handler()` body that is the same shape line-for-line: `correlation_id` → `bind` logger → health-check short-circuit → `Timer` context → `build_principal` + `require(ADMIN_SCOPE)` → method/path dispatch → a `ControlPlaneError` branch that emits `AuthzDenied` and an audit `deny` event → a final `except Exception` that logs and returns a generic 500. Only the route names and the resource verbs differ. Each copy independently suppresses `PLR0911` and re-implements the deny-audit block, so a fix to the audit or error contract must be applied in three places. A shared decorator or dispatcher in `gwcore` would collapse it.

Shows up in:
- `src/pricing_admin/handler.py:245`
- `src/routing_config/handler.py:269`
- `src/budget_admin/handler.py:164`

Cost: M

### Suppressed IaC findings without in-repo justification

The repo keeps two skip lists. `.checkov.yml` documents 22 skips, each with a one-line rationale (WAF attached via a separate association resource, audit-log versioning intentionally off, etc.). The CI step passes a longer `skip_check` string that adds six checks not present in `.checkov.yml` and with no comment anywhere: `CKV_TF_1` (Terraform no-provider-version-drift), `CKV2_AWS_50`, `CKV_AWS_115/116/117` (Lambda concurrency limit, DLQ, VPC), and `CKV_AWS_272` (Lambda code-signing). Because CI runs `soft_fail: false`, these are hard-suppressed on every scan; a reviewer reading only `.checkov.yml` would not know they are off. The divergence between the two lists is itself the debt.

Shows up in:
- `.github/workflows/ci.yml:341`
- `.checkov.yml:1`

Cost: M

### Shipped-but-undeployed lambdas

`usage_api`, `pricing_admin`, and `rate_limiter` each carry a complete Pydantic-typed handler, models, and a full test module, but no `aws_lambda_function` resource creates them (self-documented against issue #55). `pricing_admin` is the sharpest case: the admin API Gateway module references it via input variables, yet the function itself is never provisioned, so the wiring points at nothing. This is dead-code-adjacent debt with a live cost — the code is maintained, tested, and reviewed on every change while delivering no runtime value, and the partial Terraform wiring can mislead.

Shows up in:
- `src/usage_api/__init__.py:1`
- `src/pricing_admin/__init__.py:1`
- `src/rate_limiter/__init__.py:1`

Cost: L

### Duplicated ADR tree and stale post-migration statuses

The seventeen ADRs exist twice — the authoring copies under `adr/` and Starlight-rendered copies under `docs/src/content/docs/adrs/` — with identical filenames and content, kept in sync by hand (`diff` of the two file lists is empty). Separately, the ADR-017 migration reversed the Portkey engine decision, yet ADR-001 ("Portkey OSS as LLM Gateway Proxy") and ADR-006 ("Portkey dual-format API") remain **Accepted**, while only ADR-012 was re-statused to *Superseded by ADR-017*. ADR-017 itself states "The engine choice in ADR-001 aged," so the status field on the two Portkey engine ADRs now contradicts the accepted successor. *judgment-call*: worth flagging because ADRs are the primary architecture-of-record and a reader trusting the Status line would conclude Portkey is still the chosen engine.

Shows up in:
- `adr/001-portkey-oss-over-litellm.md:3`
- `adr/006-portkey-dual-format-api.md:3`
- `adr/012-response-cache-strategy.md:3`
- `docs/src/content/docs/adrs/012-response-cache-strategy.md:12`
- `adr/017-agentgateway-data-plane-spike.md:39`

Cost: M

## See also

- [architecture/module-map](../architecture/module-map.md) — 5 shared source citations
- [diagrams/structural/dependency-graph](../diagrams/structural/dependency-graph.md) — 5 shared source citations
- [architecture/system-overview](../architecture/system-overview.md) — 4 shared source citations
- [behavior/processes](../behavior/processes.md) — 4 shared source citations
- [diagrams/architecture/components](../diagrams/architecture/components.md) — 4 shared source citations
