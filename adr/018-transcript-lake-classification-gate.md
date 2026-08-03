# ADR-018: Governed transcript lake with a classification-first privacy gate

- **Status:** Proposed
- **Date:** 2026-08-03
- **Relates to:** ADR-010 (cost attribution), ADR-011 (Bedrock Guardrails), ADR-014 (two-plane split), ADR-016 (control-plane foundation), ADR-017 (agentgateway data plane)

## Decision (lead)

Add a governed transcript lake: agent/coding-assistant transcripts flowing through the gateway are captured into a short-retention quarantine zone, classified against a tenant-owned sensitive-data taxonomy, and only then promoted — as ATIF v1.7 trajectories in S3 Tables (Iceberg) — into a queryable lake for eval harvesting, SFT/RL data mining, and analytics. Classification is the core of the feature; entity-level PII redaction is a supporting module, not the headline.

The privacy boundary is **at rest, before promotion** — never in the request path. The data plane stays fast and lossless; nothing becomes queryable until the gate has run.

## Context

Organizations running coding assistants at scale want a transcript data lake (eval harvesting, failure classification, SFT corpora) but hesitate to build one, because developers incidentally discuss sensitive material with the assistant. The hard case is not entity PII: transcripts touching **employee performance management and unregretted attrition (URA/PIP)** are a representative example, and no regex or PII classifier catches them — it is a data-classification problem. Emails, ARNs, and account IDs are the easy, commodity part; "should this conversation exist in a lake at all" is the feature.

The repo already has the substrate: the inline Bedrock guardrail runs detect/log-only in the data plane (ADR-011/017), cost_attribution parses the agentgateway access log, and the `audit_log` module ships Firehose → S3 Parquet with a Glue catalog. What is missing is body capture, the classification gate, the ATIF assembler, and the governed lake itself.

## Architecture

```
agentgateway (data plane)
  │  request/response bodies + access-log identity/usage
  ▼
QUARANTINE  s3://…-transcript-quarantine/   (KMS, no human-readable principal,
  │                                          7–30 day lifecycle expiry)
  ▼
Step Functions redaction/classification pipeline (async, near-line)
  ├─ Stage 1 screen   — embeddings vs per-category seed phrases + deterministic
  │                     marking/keyword detectors (CUI banners are greppable);
  │                     tuned for recall, flags ~5–10% of traffic to Stage 2
  ├─ Stage 2 judge    — Haiku-class LLM, tenant rubric in prompt; emits
  │                     {label, confidence, quoted-span justification}
  ├─ Entity redaction — ApplyGuardrail (baseline, already IAM-wired) +
  │                     Presidio w/ tenant custom recognizers (self-hosted,
  │                     in-VPC); typed placeholders {{EMAIL_1}}, HMAC
  │                     pseudonyms (per-tenant key) so joins survive
  ├─ Secrets scan     — gitleaks/trufflehog rulepacks; redact + optional
  │                     security event (a secret in a transcript is a live leak)
  └─ ATIF assembler   — build + validate ATIF v1.7 (atif~=1.7.0 Pydantic models)
  ▼                     from bodies + cost_attribution usage/identity
PROMOTED LAKE  S3 Tables (Iceberg), Lake Formation grants
  ├─ trajectories        — 1 row/run: ATIF doc + team/model/date partitions
  ├─ steps               — exploded for SQL-level eval mining
  └─ redaction_manifest  — detector provenance, labels, confidences, actions,
                           classifier + taxonomy versions (separately permissioned)
```

## The classification taxonomy is the product

A tenant-owned policy object (AppConfig, like guardrail config) maps categories to actions:

| Category (default set)                          | Default action |
|-------------------------------------------------|----------------|
| HR: performance mgmt, URA/PIP, comp, disciplinary | **drop**       |
| Legal: privilege, litigation hold                 | **drop**       |
| Corp-dev: M&A, pre-announcement reorgs            | **drop**       |
| Security: incident details, vuln reports          | **restrict** (security-team-only partition) |
| Regulated markings: CUI, export-controlled, customer-confidential | **restrict** |
| Entity PII / secrets (any category)               | **redact**     |

Three actions, because deletion is not always right: a security-incident transcript may be exactly what the security team wants to mine, in a Lake Formation partition only they can read. **Drop** keeps a metadata stub only (team, timestamps, token counts, category label — never the justification span). The default taxonomy stays small and opinionated (the six rows above, URA/PIP named explicitly); tenants extend it, but shipping a large blank taxonomy invites a six-month governance committee.

## Shadow mode first — because "found incidentally" means the base rate is unknown

Sensitive transcripts are found incidentally, which means nobody knows what fraction of traffic is sensitive, so any classifier threshold picked up front is a guess. Rollout is therefore:

1. **Shadow (weeks 1–2+):** classify everything in quarantine, write labels + confidences to the manifest, promote nothing sensitive. Output: a measurement report — "N% of traffic touches HR topics, here is the confidence distribution, here is the proposed drop line." That report converts hesitation into a quantified, governed decision, and is itself the first deliverable an operator sees.
2. **Gated promotion:** enable promote/drop/restrict at the tuned thresholds.
3. **Continuous:** classifier version + taxonomy version stamped on every manifest row; a discovered miss becomes a taxonomy update + retroactive reclassification job over existing partitions (cheap under Iceberg: snapshot, reclassify, expire the old snapshot).

## Deliberate positions (the ones that will be challenged)

- **No in-path redaction.** Coding traffic is dense with PII-lookalikes (emails in git logs, names in blame output, account IDs in ARNs); mangling a response in flight breaks the product on a false positive. In-path stays detect/tag-only (ADR-011); enforcement is at promotion.
- **No human review queue for borderline HR content.** A reviewer reading a maybe-PIP transcript is precisely the harm this feature exists to prevent. Over-drop instead: a lost eval trajectory costs ~nothing at this volume; one URA conversation surfacing in an analyst's Athena query costs the program.
- **Tune the judge for recall, accept over-dropping.** The trust-killer is a miss in the lake, not a false drop.
- **Dropped-item manifests carry the category label only, never the quoted span.** The evidence is buried with the transcript; the manifest must not become the leak.
- **No Amazon Comprehend.** Unavailable in the target environment. Entity detection = ApplyGuardrail (managed baseline, already integrated) + Presidio (self-hosted, in-VPC, tenant custom recognizers for employee IDs / internal usernames / ticket formats). Both write to the same manifest with detector provenance so their disagreement is measurable. Honest trade: vanilla Presidio has weaker natural-language name recall than a managed NER; in coding transcripts most PII is structured, where patterns + custom recognizers do well, and the drop-not-redact judge protects the conversations that matter. If name recall becomes a real gap, Presidio swaps in a GLiNER-class NER backend, still self-hosted.
- **Attribution is a policy knob, surfaced explicitly.** Default: promoted trajectories carry team-level attribution only; user identity lives in a restricted mapping table. For the perf-management fear specifically, an unlinkable lake removes most of the chilling effect.

## Alternatives considered

- **In-path redaction at the gateway** — rejected: latency + false-positive product breakage; the gateway's job is fidelity.
- **Comprehend PII (managed NER)** — unavailable in the target environment; would anyway have covered only the commodity detector class.
- **PII-only scope (no topic classification)** — rejected: fails the motivating case outright. URA/PIP content contains no detectable entities.
- **Redact-everything instead of drop** — rejected for topic-sensitive content: the topic itself is the sensitive datum; no entity masking makes a URA conversation storable.
- **Plain S3 + Glue Parquet (reuse audit_log pattern as-is)** — workable, but S3 Tables gives managed compaction/snapshots, and Iceberg snapshots are what make retroactive reclassification cheap. The audit_log module stays as-is; the lake is a sibling, not a retrofit.

## Consequences

**Positive:** the adoption blocker inverts — a governed lake with a provable gate (manifest = audit trail: prove redaction ran without re-exposing what it removed); eval harvesting lands on a standard format (ATIF → Harbor/Athena/Spark directly); the control-plane moat argument (ADR-017) extends — attribution, budgets, and now governed transcripts are exactly what a bare data plane lacks.

**Negative / risks:** Stage-2 judge cost at high developer counts (mitigated by the Stage-1 screen and lake opt-in per team, but must be modeled in shadow mode); topic-classifier recall is unproven until shadow data exists (the design accepts over-dropping for exactly this reason); code-aware PII ambiguity (an email in `git blame` output the developer asked about is content, not incidental PII — tenant policy must pick a side, default redact); body capture increases data-plane log volume materially (quarantine lifecycle + compression bound it).

## Implementation seams (module shape)

1. `infrastructure/modules/transcript_lake/` — quarantine bucket (KMS, lifecycle, deny-human-read), S3 Tables namespace + three tables, Lake Formation grants, Step Functions pipeline, assembler Lambda. Gated by `enable_transcript_lake` (default `false`), like `enable_guardrails`/`enable_audit_log`.
2. agentgateway body capture → existing Firehose path (new stream), keyed to join with the access log cost_attribution already parses.
3. `src/transcript_gate/` — Stage-1 screen, Stage-2 judge client, Presidio runner, gitleaks runner, ATIF assembler (atif~=1.7.0), manifest writer. Classification policy as an AppConfig document per team.
4. Shadow-mode report generator (Athena over the manifest) — the first deliverable an operator sees.

## Sources

- Harbor ATIF RFC 0001 (v1.7): https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md
- `atif` Pydantic models: https://pypi.org/project/atif/
- Microsoft Presidio: https://github.com/microsoft/presidio
- Bedrock ApplyGuardrail (standalone mode): https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html
- Amazon S3 Tables: https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables.html
