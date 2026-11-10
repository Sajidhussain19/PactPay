# PactPay demo video script (2:45)

Record at 1080p (OBS, 1920x1080 canvas, browser zoom 110%). No music. Cut all waiting time;
the verification takes ~20s, show the first few checks streaming, then jump-cut to the report.
Add captions (YouTube auto-captions, then fix names: PactPay, PayPal, Playwright, LangGraph).

Before recording: open https://pactpay-web.onrender.com, click **Reset demo**, log in as the
client once so the first page load is warm.

| Time | On screen | Voiceover |
|---|---|---|
| 0:00 | Black screen, then the landing page | "Sam built a website for a bakery, delivered it, and never got paid. Clients have the opposite fear: paying up front for work that doesn't work." |
| 0:12 | Zoom on "Why not Upwork?" | "Marketplaces only protect deals on their platform and settle quality fights by hand. PactPay is escrow for any direct deal, where an AI verifies the work, with evidence." |
| 0:25 | Click **Continue as the client**. Dashboard with AG Grid and money cards | "Rosie is the client." |
| 0:30 | Open **New contract**, show the bakery job text, click **Draft the contract**, scroll the criteria (cut the wait) | "She describes the job in plain words. PactPay drafts milestones and acceptance criteria a program can actually check: the form submits, no horizontal scroll on a phone, loads under three seconds, matches her mockup. Anything subjective goes to Rosie herself." |
| 0:50 | Back to the bakery milestone, click **Instant demo funding**. Zoom on "$150.00 held by PayPal, not taken" and the authorization id | "She funds the milestone with PayPal. The money is authorized and held by PayPal, not taken. PactPay never holds funds." |
| 1:05 | **Switch to Freelancer**, click **Buggy site**, **Submit for verification**. Live timeline streams: checks tick green and red with thumbnails | "Sam delivers. PactPay opens the live site in a real browser and checks every criterion as you watch." |
| 1:18 | Report: "Some criteria failed", zoom on **What to fix**, the JS error and the 825px overflow screenshot | "The contact form throws an error and the page scrolls sideways on a phone. Sam sees exactly what to fix, with screenshots, and no money moves." |
| 1:30 | Submit **Fixed site**, jump-cut to "All automated checks passed" | "He fixes it and resubmits. Everything passes." |
| 1:40 | Zoom on one criterion: confidence, reasoning, evidence chips | "Every verdict shows its evidence, a confidence score, and the judge's reasoning. The AI can only make it harder to get paid automatically, never easier." |
| 1:50 | **Switch to Client**, **Approve and release payment**, money panel turns to "$142.50 paid to the freelancer" with capture and payout ids. **Switch to Freelancer**: "$142.50 paid to you" | "Rosie approves. PayPal captures the payment and Payouts sends Sam his share. Every call is idempotent, so a double click or a retry can never pay twice." |
| 2:05 | Reset demo, fund, submit **Sneaky site**. Red banner "Manipulation attempt detected", zoom on the hidden text snippet | "Now a sneaky freelancer. The site works, but it hides text telling the AI to ignore its instructions and mark everything passed. PactPay detects it and blocks automatic payment. A human decides." |
| 2:20 | `backend/evals/README.md` table, then `docs/security.md` | "We measured it on 49 labeled deliveries: zero false passes, zero false fails, ten of eleven manipulation attempts flagged, about two cents per verification. And we publish the case we miss." |
| 2:35 | Landing page | "PactPay: freelancers get paid, clients pay only for work that works. Built on PayPal." |

## Recording tips

- Record each scene separately and stitch; re-record a scene instead of fixing it in the edit.
- Hide bookmarks and extensions; use a clean browser profile.
- Zoom (OBS scale or an editor) on: the held-by-PayPal panel, the fix list, the paid panel, the red
  injection banner.
- Keep it under 3:00 (Devpost limit). Upload to YouTube as **Public**, not unlisted.
- Don't show PayPal's logo as your own branding, and use no copyrighted music.
