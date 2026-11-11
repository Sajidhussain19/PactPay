# Deploying PactPay to Render

About 20 minutes, most of it waiting for builds. You need a GitHub account and a Render account.
**Cost: ₹0.** Everything runs on Render's free plan (no card needed for free services).

## 1. Push the repo to GitHub (public)

1. On github.com create a new **public** repository named `pactpay`, with **no** README, license or
   .gitignore (the repo already has them).
2. In a terminal at the project root:

   ```bash
   git remote add origin https://github.com/<your-username>/pactpay.git
   git push -u origin main
   ```

3. On the repo page, click the gear next to **About** and make sure the MIT license shows.
   Add the description "AI-verified escrow for freelance work, powered by PayPal".

Your `.env` files are git-ignored, so no keys are pushed. Check with `git ls-files | grep .env`
(only `backend/.env.example` should appear).

## 2. Create the Render Blueprint

1. Render dashboard -> **New** -> **Blueprint** -> connect GitHub -> pick `pactpay`.
2. Render reads `render.yaml` and proposes two free services: `pactpay-api` (Docker) and
   `pactpay-web` (Node). There's no separate database: the demo uses SQLite inside the API container.
3. It asks for the secret values (`sync: false`). Paste the same values as in `backend/.env`:
   - `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`: the **US** sandbox app (PactPay-US)
   - `OPENAI_API_KEY`
   - `DEMO_FREELANCER_PAYPAL_EMAIL`: `sb-rrdxu53641447@personal.example.com`
   - `PAYPAL_WEBHOOK_ID`: leave empty for now (step 4)
4. Click **Apply**. The API image takes ~8-10 minutes to build the first time (it installs Chromium).

**How it fits the free plan (512 MB):** the API runs in low-memory mode (`LOW_MEMORY_BROWSER`, one
check and one verification at a time). Tested in a container capped at 512 MB with no swap: the
full demo (buggy site, fixed site, real PayPal capture and payout) peaked at about 280 MB.

**Free-plan behaviour to know:**
- The API sleeps after ~15 minutes without traffic; the first request then takes about a minute
  to wake it. Open the site a minute before recording or before judges look.
- Waking up restarts the container, which resets the demo data (SQLite) and re-seeds the bakery
  contract automatically. That's intended: every judge starts from a clean demo.
- Verifications run one at a time, so if two people verify at once, the second waits its turn.

## 3. Check the URLs

If Render gave the services the names in `render.yaml`, the URLs are
`https://pactpay-api.onrender.com` and `https://pactpay-web.onrender.com` and nothing else needs
changing. If a name was taken, Render adds a suffix. Then update these env vars to the real URLs
and redeploy both services:

- `pactpay-api`: `BACKEND_URL`, `FRONTEND_URL`, `CORS_ORIGINS` (JSON list, e.g. `["https://pactpay-web-abc1.onrender.com"]`)
- `pactpay-web`: `NEXT_PUBLIC_API_URL` (needs a rebuild, since Next.js bakes it in at build time)

Also update the demo link at the top of `README.md` and in `docs/devpost.md`.

## 4. PayPal webhook (optional, recommended)

1. developer.paypal.com -> Apps & Credentials -> **PactPay-US** -> **Add Webhook**.
2. URL: `https://pactpay-api.onrender.com/webhooks/paypal`. Events: Payment authorization created
   and voided, Payment capture completed and denied, Payouts item succeeded and failed, Payouts
   batch success.
3. Copy the **Webhook ID** into `PAYPAL_WEBHOOK_ID` on `pactpay-api` and redeploy.

Without it the demo still works: PactPay polls the payout status itself.

## 5. Test from a fresh (incognito) browser

- [ ] `https://pactpay-api.onrender.com/health` returns `"env":"production"`
- [ ] Open the web URL, click **Reset demo**, then **Continue as the client**
- [ ] Open *Landing page delivered*, click **Instant demo funding** -> "held by PayPal, not taken"
- [ ] **Switch to Freelancer**, **Buggy site** -> submit -> live checks stream -> failed with fixes
- [ ] **Fixed site** -> submit -> all automated checks passed
- [ ] **Switch to Client** -> **Approve and release payment** -> "$142.50 paid to the freelancer"
- [ ] Reset, fund, submit **Sneaky site** -> "Manipulation attempt detected"
- [ ] **Pay with PayPal** -> log in as `sb-f0bhz53641454@personal.example.com` -> approve -> returns funded
- [ ] Evidence screenshots load in the report

Render's free services sleep after inactivity; open the site a minute before recording or judging.
Evidence screenshots are stored on the container's disk, so they disappear on redeploy (just
re-run the demo).
