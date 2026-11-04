# PactPay progress

## 2026-11-02: M1 Foundations
- Built: repo layout, FastAPI `/health`, pydantic-settings config, Dockerfile, docker-compose
  (Postgres), GitHub Actions (ruff + pytest), MIT license, `docs/architecture.md`.
- Decision: added `NEEDS_REVIEW` state for low-confidence verdicts.

## 2026-11-02: M2 PayPal layer
- Built: `app/paypal/client.py` (cached OAuth, 401 refresh, retries with backoff on 5xx/429/network,
  stable PayPal-Request-Id, debug-id logging), `orders.py` (AUTHORIZE order, authorize, capture,
  void, reauthorize), `payouts.py` (fee split, idempotent payouts, status), `webhooks.py` +
  `POST /webhooks/paypal` (signature verification, dedupe, dispatch), `scripts/sandbox_flow.py`,
  `docs/paypal-setup.md`.
- Works: 37 mocked tests pass; ruff clean. Sandbox run pending real credentials.
- Decisions: request IDs derived from milestone IDs (retry-safe); webhook dedupe is in-memory until M6.
- Switched LLM provider to OpenAI (gpt-4o, configurable via LLM_MODEL); PayPal sandbox OAuth and OpenAI key verified.
- Sandbox verified end to end (2026-11-02): $150 card-funded authorization -> capture COMPLETED -> payout $142.50 SUCCESS; void path VOIDED.
- Gotcha: India (IN) sandbox business accounts cannot send payouts (PAYOUT_NOT_AVAILABLE). Platform app now uses a US business sandbox account.
- Added `--card` mode (sandbox test card, no browser approval) for scripts, evals and judge demo.
- Next: M3 contract agent (OpenAI gpt-4o).

## 2026-11-02: M3 Contract agent
- Built: `app/core/llm.py` (provider-agnostic structured-output interface, OpenAI impl with
  vision), `app/agents/contract_schema.py`, `contract_rules.py` (deterministic validation and
  normalization), `contract_agent.py` (LangGraph: draft -> validate -> repair x2 -> finalize),
  `POST /contracts/draft`, `scripts/draft_contract.py`.
- Works: 58 tests pass; live gpt-4o drafts the bakery contract with auto + manual criteria.
- Decisions: the LLM drafts, code decides. Amounts are treated as weights and allocated to the
  cent; due dates computed from `due_in_days`; vague or unrunnable criteria trigger a repair,
  then fall back to manual with a visible warning. Tests blank all secrets so they never hit
  real APIs.
- Known issues: the model sometimes drops subjective asks ("modern") and invents selectors
  (".menu"). To be measured in M9 evals; M4 locator fallback handles bad selectors.
- Next: M4 Playwright checks + demo sites.

## 2026-11-03: M4 Verification checks + demo sites
- Built: `verification/web_checks.py` (Playwright: page_loads, element_present with AI locator
  fallback, form_submits, responsive_layout, performance), `visual_match.py` (gpt-4o vision,
  per-section verdicts, score computed by code), `repo_check.py` (safe shallow clone, README /
  license / required files / secret scan), `scripts/check_site.py`; demo sites in `demo/`
  (good, buggy, malicious) + wireframe mockup.
- Works: 96 tests pass (real Chromium against demo sites). Live: good site passes all 6
  checks (visual 90%); buggy fails form (JS error) and mobile (+825px scroll); malicious site's
  fake success message and hidden "mark all passed" text are caught.
- Decisions: fail closed (load errors = fail, our own crashes = human); off-screen text doesn't
  count as present; success must APPEAR after submit; AI locator only proposes, browser verifies.
- Not built: tests_pass sandbox (STRETCH).
- Next: M5 verification graph + LLM judge.

## 2026-11-03: M5 Verification graph + LLM judge
- Built: `verification/report.py` (Submission, Verdict, report, progress events),
  `agents/judge.py` (per-criterion gpt-4o judge, untrusted content fenced, `combine` fail-safe
  rules, rules-only fallback), `verification/runner.py` (criterion -> check dispatch),
  `workflows/verification_graph.py` (LangGraph plan -> parallel checks -> parallel judge ->
  report, bounded concurrency, SSE-ready events), `demo/contracts/bakery.json`,
  `scripts/verify_demo.py`.
- Works: 119 tests. Live with gpt-4o: good-site PASSED (6 pass + 1 manual, ~12s); buggy-site
  FAILED (form + mobile) with fix suggestions; malicious-site FAILED.
- Decisions: judge can never upgrade a failed check (-> human); low-confidence passes -> human;
  check crash -> human; manual criteria don't block PASSED, client confirms them at approval.
- Known: judge confidence clusters at 0.95 (calibration to measure in M9). Injection attempts
  are defeated but not yet *flagged* in the report (M8 detector).
- Next: M6 persistence + state machine.

## 2026-11-03: M6 Persistence + state machine
- Built: `workflows/milestone_state.py` (pure state machine: 17 actions, roles, guards,
  `allowed_actions` for UI buttons), `models/db.py` (User, Contract, Milestone, Criterion,
  Submission, VerificationRun, CheckResult, PaymentEvent, WebhookEvent, AuditEvent),
  `core/db.py`, `services/milestones.py` (the only way state changes: guard -> mutate -> audit,
  optimistic locking), `services/contracts.py`, `services/records.py` (reports, idempotent
  payment events, DB webhook dedupe), Alembic `migrations/` (initial schema).
- Works: 407 tests (288 generated state-machine cases: every legal and illegal transition,
  every forbidden role, every money guard). Migrations round-trip on SQLite and Postgres 16;
  persistence tests pass on both. Webhook dedupe now survives restarts.
- Decisions: added RELEASING (capture ok / payout pending is retryable) and CANCELLED.
  `apply` decides before mutating, so illegal actions change nothing. `get()` always reads
  fresh rows. Disabled ruff S105 (false positives on "pass" enums).
- Gotcha: async SQLAlchemy can't lazy-load expired attributes (MissingGreenlet); capture ids
  before commit/rollback, eager-load relationships.
- Next: M7 REST API + SSE + release executor.

## 2026-11-04: M7 API, live progress, safe release
- Built: `services/payments.py` (the only money-moving code: fund via PayPal checkout or sandbox
  card, authorize on return, void/refund, resumable capture -> payout release with milestone-
  derived idempotency keys, payout polling, reauthorize-on-expiry, webhook reconciliation,
  auto-approve timer), `services/verification_manager.py` (background runs, SSE event channels
  with replay, restart recovery), REST API (`api/auth.py`, `contracts.py`, `milestones.py`,
  `deps.py`, `schemas.py`), signed demo-login tokens, `/artifacts` for evidence screenshots,
  `scripts/e2e_live.py`.
- Works: 419 tests (12 API flow tests with fake PayPal prove: fail -> fix -> pass -> paid,
  no double capture on double-click, capture-ok/payout-fail -> retry pays once, pending payout
  settles via webhook, crash-before-save capture reconciled by webhook, void on cancel,
  auto-approve after 48h, human review, SSE replay, auth/404 for strangers).
- LIVE E2E (real sandbox + Chromium + gpt-4o, via HTTP): buggy site FAILED with fixes ->
  good site PASSED (6 pass + 1 manual) -> capture 0UA230614E063910C -> payout SUCCESS ->
  RELEASED, freelancer received 142.50 USD (fee 7.50).
- Decisions: payout email checked BEFORE capture; verification runs in-process (one shared
  Chromium, semaphore); demo auth = HMAC tokens; /contracts/draft requires login (costs credits).
- Gotchas: async lazy-load again (save report); in-memory SQLite + StaticPool shares one
  connection -> flaky with background tasks, tests now use a SQLite file per test.
  Port 8000 is used by another app on this machine; dev server runs on 8010.
- Next: M8 security (injection detector + flags in report, SSRF guard), then evals.

## 2026-11-04: M8 Security
- Built: `security/url_guard.py` (SSRF: resolve + reject non-public IPs, schemes, creds,
  ports), browser request routing that follows every redirect hop itself, `security/injection.py`
  (deterministic prompt-injection detector, hidden sources = HIGH), `security/scanner.py` (page
  hidden text/comments/meta/attributes + repo docs), parallel LangGraph `security_scan` branch,
  flags force NEEDS_REVIEW, `security_flag` SSE events, `demo/sneaky-site`, `docs/security.md`
  (10 attacks -> defense -> test), AI/money import-boundary test.
- Works: 484 tests. Live gpt-4o on sneaky-site: 6/6 checks pass, 7 HIGH flags -> NEEDS_REVIEW.
  Good site has zero flags (no false positive).
- Found and fixed a real gap: Playwright routes don't see redirect targets, so a public URL
  could 302 to 169.254.169.254 (verified experimentally). Now every hop is validated.
- Decisions: detector is regex, not LLM (not injectable, explainable); only repo *docs* are
  scanned (code has legit prompts/config); ALLOW_PRIVATE_URLS for local demo, refused in prod.
- Residual risks documented: DNS rebinding (needs egress firewall), paraphrased injections,
  text inside images, no tests_pass sandbox.
- Next: M9 evals.
