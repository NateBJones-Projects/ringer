# Operational Risk Audit: Ringer routine_id / goal_id Projection

## Scope

This is a read-only review of the live Ringer checkout, the active hook at
/Users/hermes/.ringer/hooks/paperclip_projector.py, relevant manifest/documentation/test surfaces, and
JAC-3487 (303e72fa-2be7-44b2-a28b-68900708da84). No projector execution, Paperclip POST/PATCH, Beads write,
service change, git mutation, or test run was performed.

Evidence commands included line-numbered source inspection with nl -ba, targeted rg searches, a read-only
diff/hash comparison, and curl GET probes. The first JAC-3487 GET returned HTTP 200 at
2026-07-15T04:08:40Z without an Authorization header. A later read-only probe at 04:11:16Z failed to
connect while lsof showed a node listener; this is recorded as an observed connectivity caveat, not assigned
as an application defect.

The live JAC-3487 response identified:

- identifier: JAC-3487; status: in_progress.
- companyId: 87c32b8e-f131-4df8-ad8e-963d01b458e7; projectId:
  f99704da-43b5-4cf7-89ef-13f59a348d90; goalId: null.
- The objective is to add routine_id and goal_id, project verdicts to issue plus Routine/Goal comments,
  provide templates/routine-fanout.json, display references in Ringside, and complete an end-to-end run.
- The response showed blocked dependencies JAC-3442 and JAC-3485, with JAC-3473 done.

## Trust Boundaries

1. **Manifest/run-state input -> Ringer parser.** A manifest is operator/job input. TaskSpec.from_obj accepts
   selected fields and silently ignores unknown fields. routine_id and goal_id are not fields of TaskSpec
   (/Users/hermes/ringer/ringer.py:384-404, 405-464). The sample manifest nevertheless contains both fields
   (/Users/hermes/ringer/swarms/routines-goals-demo/manifest.json:7-25).

2. **Ringer -> installed post-run hook.** Ringer invokes the regular hook only when a task has
   paperclip_issue or bead_id (/Users/hermes/ringer/ringer.py:7092-7116). A routine/goal-only task therefore
   does not reach the active hook. The invoked file is the mutable regular file
   /Users/hermes/.ringer/hooks/paperclip_projector.py, not a symlink to the checkout.

3. **Hook input -> local filesystem.** The hook accepts arbitrary command-line run-state and manifest paths,
   resolves them, and reads JSON (/Users/hermes/.ringer/hooks/paperclip_projector.py:362-380). No restriction to
   Ringer's state directory or to a trusted run is enforced.

4. **Hook -> Paperclip HTTP API.** PAPERCLIP_URL is an environment-controlled base URL, defaulting to
   http://127.0.0.1:3100 (.../paperclip_projector.py:387-388). Routine, goal, and issue identifiers are
   interpolated into URL paths or query strings. The hook does not verify the target company/project before
   posting.

5. **Paperclip response -> destination selection.** A routine response's activeIssue is trusted as an issue ID
   (.../paperclip_projector.py:253-266). Goal resolution asks one company-scoped issue query and uses the first
   result (.../paperclip_projector.py:269-283). This is a target-selection boundary, not merely a lookup.

6. **Ringer verdict -> comments/audit log.** The hook reads the finished run-state and appends comments and a
   local JSONL projection log. It does not update the Ringer state file or Paperclip issue status.

## Observed Controls

### Controls that are present

- HTTP calls have finite 15-second urlopen timeouts (.../paperclip_projector.py:48, 228, 245). The parent
  Ringer hook subprocess has a 30-second timeout (/Users/hermes/ringer/ringer.py:7113-7116).
- The Ringer state writer finishes before the post-run hook is attempted (/Users/hermes/ringer/ringer.py:7077-7080);
  the projection code is comment-oriented rather than a verdict/status update.
- extract_cross_links de-duplicates identical four-field link tuples within one invocation
  (.../paperclip_projector.py:168-216). This does not provide retry idempotency.
- Missing routine active issues and missing goal in-progress issues are explicitly represented as skipped,
  non-fatal results (.../paperclip_projector.py:290-319).
- The hook records timestamp, run identity, state, cross-links, and result summaries in
  ~/.ringer/hooks/projection_log.jsonl (.../paperclip_projector.py:442-455).
- The source comments and README document the intended fields (/Users/hermes/ringer/README.md:107-110), and the
  active hook contains routine/goal functions (.../paperclip_projector.py:219-359).

### Controls that are absent or insufficient

- **Authentication:** post_paperclip_comment sends only Content-Type; GET sends only Accept; PATCH sends only
  Content-Type (.../paperclip_projector.py:37-53, 223-250). There is no Authorization header and no
  PAPERCLIP_API_KEY lookup. The defined _paperclip_api_patch is unused (.../paperclip_projector.py:234-250).
- **Identifier validation:** there is no UUID/allowlist validation for routine_id, goal_id, paperclip_issue,
  returned activeIssue, or PAPERCLIP_COMPANY_ID. The parser, where it handles existing cross-links, only
  type-checks strings and strips whitespace (/Users/hermes/ringer/ringer.py:444-464).
- **URL validation:** PAPERCLIP_URL is not constrained to an approved host, scheme, or loopback/HTTPS policy;
  identifiers are not URL-encoded before insertion into path/query strings.
- **Scope verification:** no fetched target is checked for expected company, project, routine/goal ownership, or
  relationship to the originating run. The company is taken from the first routine lookup or an environment
  fallback (.../paperclip_projector.py:390-403).
- **Idempotency:** comments contain a run ID but there is no read-before-write marker, idempotency key, or duplicate
  detection. Each retry/re-run calls POST again (.../paperclip_projector.py:413-439).
- **Failure signaling:** per-destination errors are converted to result strings and main() still returns 0
  (.../paperclip_projector.py:405-457). The Ringer wraps the entire hook call in contextlib.suppress(Exception) and
  does not fail the run on a nonzero hook return (/Users/hermes/ringer/ringer.py:7096-7120).
- **Acceptance coverage:** no test file matched paperclip_projector, routine_id, goal_id, or projection; the
  only matching existing test was tests/test_paperclip_to_ringer_sync.py. No
  /Users/hermes/ringer/templates/routine-fanout.json exists, and no HUD/dashboard source match for
  routine_id or goal_id was found.
- **Deployment provenance:** the active hook is 460 lines/16,861 bytes and SHA-256
  557dbbdc...bfaae027; the tracked checkout hook is 247 lines/8,640 bytes and SHA-256
  2dd421e8...4528980d8a. The checkout is dirty on branch
  fleet-sync/paperclip-to-ringer-20260709 (README, docs, registry changes plus untracked runtime artifacts).
  The active implementation is therefore not proven to be the reviewed git revision.

## Risk Register

Severity is ranked for the current live shape. Scenarios are analytical and were not executed because this review
was explicitly read-only.

| Rank / severity | Exploit or failure scenario | Concrete evidence | Impact | Minimum mitigation |
|---|---|---|---|---|
| 1 — **Critical** | A local process supplies a valid-looking target and causes a comment to be sent to an unintended issue/company, or sets PAPERCLIP_URL to an unapproved receiver. A future bearer secret would also be at risk of being sent to that receiver. | No Authorization header or key handling (.../paperclip_projector.py:37-53, 223-250); raw PAPERCLIP_URL (:387) and raw IDs in URL construction (:39, :225, :236, :278). Initial unauthenticated GET to JAC-3487 returned 200 on loopback. | Unauthorized or misdirected operational comments; leakage of run identifiers, local artifact paths, verdict metadata, and target IDs. | Require an approved Paperclip origin; reject non-approved scheme/host; validate UUIDs/issue identifiers; use a secret-backed, least-privilege Authorization header; never log credentials or send them to an arbitrary URL. |
| 2 — **High** | Paperclip is unavailable, authentication is rejected, or a POST times out. The Ringer run remains successful and the operator sees no hard projection failure. | Hook catches request errors and returns error dictionaries (.../paperclip_projector.py:47-53, 247-250); main() returns 0 after logging (:442-457); runner suppresses hook exceptions and ignores a nonzero return (/Users/hermes/ringer/ringer.py:7096-7120). | Silent divergence between immutable Ringer evidence and Paperclip governance state; acceptance can be falsely marked complete. | Define required vs best-effort destinations; emit a durable failed receipt and nonzero gate for required projection; retry through an authenticated bounded outbox with operator-visible status. |
| 3 — **High** | A routine/goal from company A resolves or posts to an issue in company B, or a goal comment lands on an arbitrary first in-progress issue. | Company comes from only the first routine lookup or env (.../paperclip_projector.py:390-403); direct issue POST is not company-scoped (:39); activeIssue is trusted (:260-263); goal lookup uses result[0] with no relationship verification (:277-282). | Cross-company data contamination and misleading governance history. | Resolve each target independently; fetch and assert target companyId, expected project, target relationship, status, and run binding; fail closed on ambiguity or mismatch. |
| 4 — **High** | A retry after a network timeout or an operator re-runs the hook posts the same verdict repeatedly. Timestamp variation defeats naive text matching. | POST is unconditional (.../paperclip_projector.py:413-439); comments include a generated timestamp (:124, :337-338, :357-358); de-duplication is only within one invocation (:203-216). | Comment flooding, noisy audit trails, and loss of confidence in the authoritative projection. | Use a deterministic marker such as destination + run ID + target and source digest; perform idempotent upsert/read-before-write; classify an existing matching comment as success. |
| 5 — **High** | A routine/goal-only task runs, but no projection happens at all; a mixed task can appear to satisfy the acceptance test through its ordinary issue comment while routine/goal behavior remains untested. | TaskSpec has only paperclip_issue/bead_id (/Users/hermes/ringer/ringer.py:399-404); runner trigger checks only those two fields (:7101-7106); README/demo claim the absent fields (README.md:109-110, demo :14-24). | Core JAC-3487 behavior is absent on the normal path; an end-to-end claim can be false. | Add typed fields, strict schema validation, runner trigger coverage, HUD/template support, and dedicated tests including routine/goal-only and mixed links. |
| 6 — **High** | One destination succeeds, another fails, or a stale/missing active issue is selected. The hook continues and reports overall success. | Destination loop is sequential with no transaction/compensation (.../paperclip_projector.py:407-439); missing targets are skipped (:300-319); routine fallback is documented but returns None unconditionally (:264-266). | Partial, misleading governance state; operators cannot tell whether issue, routine, and goal projections agree. | Record per-destination durable state and source digest; expose partial status; reconcile failed destinations; fail the required gate until all required destinations are confirmed. |
| 7 — **Medium** | A goal query returns an old or ambiguous in-progress issue; a routine's activeIssue is stale. The hook posts a valid-looking comment to the wrong lifecycle record. | Goal function claims “most recent” but neither sorts nor verifies recency (.../paperclip_projector.py:269-283); routine function only accepts activeIssue and does not implement its stated fallback (:253-266). | Misattributed progress and inaccurate operator decisions. | Require an exact run/routine/goal linkage and freshness window; reject missing, stale, or multiple candidates; record the resolution evidence. |
| 8 — **Medium** | A manually invoked hook reads an arbitrary JSON file and projects attacker-controlled state, verdict text, Markdown, or paths. | Arbitrary resolved CLI paths (.../paperclip_projector.py:367-378); raw run fields are interpolated into comments (:70-126, :322-359); no final-state or provenance check. | Spoofed comments, misleading verdicts, Markdown/link injection, and local path disclosure. | Restrict input paths to a trusted Ringer state root; require a finished state and source/run binding; escape Markdown; store and verify a source-state digest. |
| 9 — **Medium** | Projection evidence is incomplete or cannot be tied to the exact destination. The audit log is readable by all local users and has no integrity/locking policy. | Log records only summary fields/results (.../paperclip_projector.py:442-455); current file is mode 0644 and tail entries show Beads summaries, not HTTP method/status/URL/selected issue; no response IDs, auth identity, source digest, or retry count. | Forensics and reconciliation are weak; local users can read run metadata; concurrent writers may produce ambiguous evidence. | Use structured per-attempt records with redacted endpoint, status, target IDs, response IDs, source digest, and retry number; restrict permissions; use an append-only or centralized integrity-protected sink. |

## Failure Semantics

### Authorization and secret handling

**Observed:** The hook has no Paperclip credential path. Its HTTP requests contain only content negotiation and
content type headers. The unauthenticated JAC-3487 GET succeeding on loopback demonstrates the current local service
can be queried without a bearer header; it does not establish remote exposure. The later connection failure makes
the live service path operationally unstable or environment-sensitive.

**Recommendation:** Treat loopback reachability as transport location, not authorization. Require explicit service
authentication and a host/origin allowlist. Keep the key outside manifests, run-state, comments, and projection logs.

### Identifiers, paths, and URLs

**Observed:** IDs are only truthy/type-checked strings on the Ringer side and are concatenated into URL path/query
strings by the hook. There is no UUID grammar, URL encoding, target ownership check, or trusted-base-URL check.
The hook's input JSON paths are also unrestricted when invoked directly.

**Recommendation:** Validate at entry, business-resolution, and transport layers: strict identifier schemas;
canonical URL parsing; approved Paperclip origin; exact company/project binding; and a trusted run-state root.

### Duplicate comments and retries

**Observed:** The hook posts comments unconditionally. Its only de-duplication is for identical link tuples in one
process. There is no retry loop, backoff, or idempotency key. A timeout after the server accepted the POST is therefore
especially likely to produce a duplicate on retry.

**Recommendation:** Make the comment identity deterministic (source run ID, destination type, target ID, and
source verdict digest), and treat a previously recorded matching identity as success.

### Target resolution and stale issues

**Observed:** Routine resolution only accepts routine.activeIssue; the stated origin-based fallback is not
implemented. Goal resolution queries status=in_progress and chooses the first result. Neither path proves that
the selected issue belongs to the same company/project or run.

**Recommendation:** Resolve exact target objects and validate their ownership, relationship, lifecycle state, and
freshness. Missing or ambiguous targets should be a gate failure, not a successful skip, when projection is required.

### Partial success, timeout, and network failure

**Observed:** Issue, Beads, routine, and goal destinations are attempted sequentially. An error in one does not stop
the others. Each HTTP request can consume up to 15 seconds, while the parent allows 30 seconds for the entire hook.
There is no durable retry/outbox protocol. The hook returns 0 after partial failure, and the parent suppresses
exceptions.

**Recommendation:** Separate required and advisory destinations; persist a per-destination state machine; use
bounded retries with explicit timeout budgets; surface partial, retrying, and failed states; and make the
operator gate depend on confirmed required destinations.

### Hook fail-open and verdict preservation

**Observed:** Ringer writes/finishes its state before invoking the hook, and the projector does not call its defined
PATCH helper. No reviewed code path mutates the Ringer verdict or issue status. This is a positive preservation control.
However, the hook accepts arbitrary run-state JSON and does not verify state == finished, source provenance, or a
content digest before presenting the values as an automated projection.

**Recommendation:** Keep the Ringer verdict immutable and projection-only, but bind every comment to a verified,
finished state snapshot. A projection failure must not rewrite the verdict; it must create a visible failed receipt
and block only the external-governance acceptance gate.

## Governance Gates

The following gates should be required before JAC-3487 or a production Routine/Goal projection is accepted:

1. **Contract gate:** Add routine_id and goal_id to TaskSpec; reject wrong types, malformed UUIDs, empty
   identifiers, unknown target shapes, and unsafe URLs. Ensure routine/goal-only tasks trigger the hook.
2. **Surface gate:** Provide and lint templates/routine-fanout.json; add Ringside rendering; add tests for positive,
   negative, backward-compatible, mixed-destination, and routine/goal-only manifests. Do not count the current demo
   as proof because it has not been executed in this review and the normal parser currently drops its new fields.
3. **Authorization gate:** Use a configured Paperclip origin and least-privilege secret-backed auth; test missing,
   invalid, and rotated credentials without exposing values. Confirm logs contain no secret material.
4. **Scope gate:** For every routine and goal, verify company, project, target relationship, lifecycle status,
   freshness, and exact run binding. Reject cross-company, stale, missing, and ambiguous targets.
5. **Idempotency gate:** Demonstrate retry after an accepted-but-timeout POST produces one logical comment per
   destination, not duplicates.
6. **Failure gate:** Demonstrate issue/routine/goal partial failures, network timeout, API 4xx/5xx, and missing active
   issue. Required projection must be visibly failed or partial and must not make the Ringer run appear failed or
   alter its immutable verdict.
7. **Evidence/provenance gate:** Record the source run-state digest, installed hook hash, target resolution, response
   IDs/statuses, retry count, and per-destination outcome. Reconcile the active hook with a committed, reviewed source
   revision before treating the implementation as governed.
8. **End-to-end gate:** Run a disposable three-task Routine/Goal fixture and verify exact issue, routine-linked issue,
   and goal-linked issue comments, duplicate behavior, target scope, and immutable Ringer state. The current live
   JAC-3487 response and the dirty checkout do not constitute this evidence.

## Final Verdict

The current implementation is **not acceptable for governed Routine/Goal projection**. The core path is incomplete:
the normal Ringer schema and trigger do not carry routine_id or goal_id; the active hook lacks authentication,
strict target validation, cross-company binding, idempotency, and fail-closed signaling; and the installed hook is not
proven to match the dirty checkout. The Ringer verdict itself appears preserved because the hook only reads the
finished state and posts comments, but that positive property does not compensate for silent or misdirected external
projections.

READ_ONLY — NOT_ACCEPTABLE
