# PayPal sandbox setup

Do this once. Values verified against developer.paypal.com as of Nov 2026; re-check the dashboard if
a menu has moved.

## 1. Sandbox app and credentials
1. Log in at https://developer.paypal.com and switch the toggle to **Sandbox**.
2. **Apps & Credentials → Create App**. Name it `PactPay`, type **Merchant**, and pick the sandbox
   business account it should belong to (this is the *platform* account that receives captures).
3. Copy **Client ID** and **Secret** into `backend/.env` as `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`.
4. In the app's features list, make sure **Payouts** is enabled (sandbox usually allows it; live
   requires approval from PayPal).

## 2. Sandbox accounts
**Testing Tools → Sandbox Accounts → Create account**:

| Role | Type | Used for |
|---|---|---|
| Platform | Business | Owns the app; receives captured funds, sends payouts |
| Client | Personal | Approves and pays orders in the checkout popup |
| Freelancer | Personal | Receives payouts |

Open each account's **View/edit** to get its email and password. Give the client a balance.

## 3. Run the money flow
```bash
cd backend
python -m scripts.sandbox_flow --amount 150 --freelancer-email <freelancer sandbox email>
python -m scripts.sandbox_flow --amount 150 --void
```
Log into https://www.sandbox.paypal.com as the freelancer to see the payout arrive.
Add `--card` to skip the browser approval (funds with a sandbox test card).

**Use US sandbox accounts.** An India (IN) business account fails payouts with `PAYOUT_NOT_AVAILABLE`.

## 4. Webhooks (local)
1. Start the API: `uvicorn app.main:app --reload`
2. Expose it: `ngrok http 8000` and copy the `https://...ngrok-free.app` URL.
3. In your app: **Webhooks → Add Webhook**, URL `https://<ngrok>/webhooks/paypal`, subscribe to:
   `PAYMENT.AUTHORIZATION.CREATED`, `PAYMENT.AUTHORIZATION.VOIDED`, `PAYMENT.CAPTURE.COMPLETED`,
   `PAYMENT.CAPTURE.DENIED`, `PAYMENT.PAYOUTS-ITEM.SUCCEEDED`, `PAYMENT.PAYOUTS-ITEM.FAILED`,
   `PAYMENT.PAYOUTSBATCH.SUCCESS`.
4. Copy the **Webhook ID** into `PAYPAL_WEBHOOK_ID` and restart the API.
5. Run the sandbox flow; real events arrive signed and pass verification.

**Webhook simulator:** simulator events are not signed for your webhook ID, so
`verify-webhook-signature` rejects them. To try the simulator locally only, set
`PAYPAL_WEBHOOK_SKIP_VERIFY=true` (the app refuses to start with it in production).

## Authorization timing (affects long milestones)
Per PayPal docs an authorization has a **3-day honor period** (funds guaranteed) and stays valid for
**29 days**. You can reauthorize once after the honor period. Milestones longer than 29 days need the
client to re-fund. Check https://developer.paypal.com/docs/checkout/standard/customize/authorization/
for current rules before the demo.
