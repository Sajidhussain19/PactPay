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
- Next: run sandbox flow with real creds; M3 contract agent.
