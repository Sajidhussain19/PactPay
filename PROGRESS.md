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
