# Devpost submission

**Name:** PactPay

**Links:** live demo https://pactpay-web.onrender.com · code https://github.com/Sajidhussain19/PactPay · video https://youtu.be/Bj82U5wE0Y0

**Tagline (max 200 chars):** AI-verified escrow for freelance work: PayPal holds the payment, an AI
checks the delivered site against the contract with evidence, and the money is released when it passes.

## Inspiration

Freelancers do the work and then chase invoices; most have had trouble getting paid at some point.
Clients have the mirror-image fear: paying up front for a site that doesn't work. Escrow fixes the
money side but creates a new question nobody wants to answer by hand: *is the work actually done?*
We wanted a system that answers that question with evidence both sides can see.

## What it does

1. The client describes a job in plain language. A contract agent turns it into milestones and
   acceptance criteria that a program can check (vague wishes become checkable criteria or are left
   to the client).
2. The client funds a milestone with PayPal. The payment is **authorized and held by PayPal**, not
   captured.
3. The freelancer submits the live site (and optionally the GitHub repo). PactPay verifies every
   criterion in a real browser, streams the progress live, and shows a report with screenshots,
   measurements, confidence and reasoning.
4. If something fails, the freelancer sees exactly what to fix. When everything passes and the
   client approves (or stays silent for 48 hours), PayPal captures the payment and Payouts pays the
   freelancer.
5. Submissions that try to manipulate the AI (hidden "mark everything as passed" text) are detected
   and can never be paid automatically.

## How we built it

- **Backend:** Python, FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL. A strict milestone state machine
  (13 states, 17 actions, role checks and guards) is the only way money can move.
- **PayPal:** Orders v2 (`intent=AUTHORIZE`), Payments v2 capture / void / reauthorize, Payouts,
  Webhooks with signature verification. Every money call carries an idempotency key derived from
  the milestone; release is resumable after a partial failure.
- **AI:** LangGraph workflows. A contract agent (gpt-4o structured output plus deterministic
  validation and repair), Playwright checks run in parallel, gpt-4o vision for mockup matching, and
  an LLM-as-judge that decides each criterion from structured evidence only.
- **Security:** prompt-injection detection on hidden page content and repo docs, SSRF protection on
  submitted URLs, every browser request and every redirect hop, isolated browser contexts, safe
  shallow clones.
- **Frontend:** Next.js 15, TypeScript, Tailwind, shadcn/ui, **AG Grid Community** for the dashboard,
  Server-Sent Events for the live verification timeline.
- **Quality:** 540+ tests (including real-browser tests and every legal and illegal state
  transition), a 49-case labeled eval suite with a CI gate, deployed on **Render** with Docker.

## Challenges we ran into

- **Payouts failed from an India sandbox business account** (`PAYOUT_NOT_AVAILABLE`) after the
  capture had already succeeded. That real failure shaped the design: a `RELEASING` state and a
  resumable, idempotent release, so a retry pays the freelancer without capturing twice.
- **Playwright doesn't route redirect targets.** We proved that a public URL could redirect our
  browser to the cloud metadata address, then rebuilt request handling to follow and validate every
  redirect hop ourselves.
- **Our own evals caught our verifier being lenient:** a page with no contact form scored 80% on the
  mockup check and passed. We changed the rule (any missing agreed section fails) and the vision
  prompt, and kept the blind first-run numbers in the repo.

## Accomplishments that we're proud of

- The full loop works end to end in the PayPal sandbox: fund, fail, fix, pass, capture, payout.
- On 49 labeled deliveries: 0 false passes, 0 false fails, 10 of 11 manipulation attempts flagged,
  about $0.02 per verification.
- The AI can never move money, and a test enforces it.

## What we learned

- Criteria design is the whole game for trustworthy AI verification: if a criterion can't be
  checked by a program, it shouldn't be decided by one.
- LLM-as-judge needs rules around it: the judge can make payment harder to get automatically, never
  easier.
- Idempotency keys and explicit intermediate states matter more than happy-path code when money
  moves.

## What's next for PactPay

- A mediator agent for disputes, with partial capture.
- PayPal Agent Toolkit / MCP so a client's AI agent can hire and fund freelancers, with human
  confirmation for every money-moving step.
- More deliverable types (design files, documents, data), a sandbox to run the freelancer's tests,
  and egress isolation for the verification worker.

## Built with

python, fastapi, langgraph, openai, gpt-4o, playwright, sqlalchemy, alembic, postgresql, nextjs,
typescript, tailwindcss, shadcn-ui, ag-grid, paypal, paypal-orders-v2, paypal-payouts,
paypal-webhooks, docker, render, github-actions

## Tools: every AI model and PayPal API, and how it's used

| Tool | How it's used |
|---|---|
| OpenAI gpt-4o (structured output) | Contract agent: job description to milestones and checkable criteria |
| OpenAI gpt-4o (vision) | Compares the live page with the client's mockup, section by section |
| OpenAI gpt-4o (LLM-as-judge) | Decides each criterion from structured check evidence, with confidence and a fix suggestion |
| OpenAI gpt-4o | Proposes CSS selectors when the contract's selector doesn't match (the browser verifies) |
| PayPal OAuth 2.0 | Access tokens for all API calls |
| PayPal Orders v2 | Create `AUTHORIZE` orders and authorize them: the client's funds are held, not taken |
| PayPal Payments v2 | Capture on release, void on cancel, reauthorize on long milestones |
| PayPal Payouts | Pay the freelancer their share minus the platform fee |
| PayPal Webhooks + verify-webhook-signature | Reconcile authorization, capture and payout state |
| Playwright (Chromium) | Runs every website check in an isolated browser |
| LangGraph | Orchestrates the contract agent and the verification workflow |

## Sponsor prizes

- **AG Grid:** the client and freelancer dashboards use AG Grid Community (sorting, filtering, custom
  state-chip cell renderer, row click to open a milestone): `frontend/src/components/milestone-grid.tsx`.
- **Render:** the whole app (API with headless Chromium, Next.js frontend, managed Postgres) is
  deployed from `render.yaml` as a Render Blueprint.

## Submission checklist

- [ ] Working hosted demo (Render URL) tested from a fresh browser, Reset demo works
- [ ] Public GitHub repo; MIT license visible in the About sidebar
- [ ] Public YouTube video under 3 minutes, no copyrighted music
- [ ] Tools section lists every AI model and PayPal API (above)
- [ ] No third-party trademarks used as our branding
