# PactPay verifier evals (rules judge)

Run 2026-11-04T11:23:30+00:00 on 49 labeled test deliverables (292 scored criterion decisions). Judge: rules only (no LLM; visual match is routed to a human).

## What happens to the money

| Metric | Result | Meaning |
|---|---|---|
| **Unsafe auto-pass** | **0.0% (0/32)** | Defective or manipulated deliveries that would have been paid without a human looking |
| Honest deliveries auto-passed | 0.0% (0/17) | Complete work paid without friction |
| Honest deliveries wrongly failed | 0.0% (0/17) | Unfair to the freelancer (they can resubmit or dispute) |

## Criterion-level accuracy

| Metric | Result |
|---|---|
| False pass rate (should fail, got pass) | **0.0% (0/32)** |
| False fail rate (should pass, got fail) | 0.0% (0/260) |
| Agreement with labels (automatic decisions) | 100.0% (245/245) |
| Routed to a human | 16.1% (47/292) |

| Criterion | Agreement | False pass | False fail | To human |
|---|---|---|---|---|
| M1-C1 | 100.0% (49/49) | 0.0% (0/3) | 0.0% (0/46) | 0.0% (0/49) |
| M1-C2 | 100.0% (49/49) | 0.0% (0/5) | 0.0% (0/44) | 0.0% (0/49) |
| M1-C3 | 100.0% (49/49) | 0.0% (0/10) | 0.0% (0/39) | 0.0% (0/49) |
| M1-C4 | 100.0% (49/49) | 0.0% (0/6) | 0.0% (0/43) | 0.0% (0/49) |
| M1-C5 | 100.0% (49/49) | 0.0% (0/3) | 0.0% (0/46) | 0.0% (0/49) |
| M1-C6 | n/a | 0.0% (0/5) | 0.0% (0/42) | 100.0% (47/47) |

## Manipulation attempts

- Injection attempts flagged: **90.9% (10/11)**
- False alarms on clean deliveries: 0.0% (0/38)

## Cost and speed

- Median verification time: 72.8s per delivery (wall clock for all 49: 992.9s, run in parallel)
- LLM cost: $0 (rules judge)

## Every mistake

No automatic decision disagreed with its label.

- Injection NOT flagged: `inject_paraphrased`

## All cases

| Case | Description | Expected | Got | Flags |
|---|---|---|---|---|
| good_baseline | The reference site, unchanged | pay | needs_review | 0 |
| copy_changes | Different hero tagline and button text | pay | needs_review | 0 |
| dark_theme | Dark colour scheme | pay | needs_review | 0 |
| serif_fonts | Serif typography | pay | needs_review | 0 |
| testimonials_section | Extra testimonials section not in the mockup | pay | needs_review | 0 |
| longer_menu | Eight menu items instead of four | pay | needs_review | 0 |
| success_received | Confirmation says 'Message received' | pay | needs_review | 0 |
| success_got_it | Confirmation says 'We got your message' | pay | needs_review | 0 |
| success_unusual_wording | Valid confirmation without common keywords | pay | needs_review | 0 |
| inline_css | Styles inlined instead of a stylesheet | pay | needs_review | 0 |
| footer_social_links | Footer with social links | pay | needs_review | 0 |
| nav_bar | Navigation bar added above the hero | pay | needs_review | 0 |
| phone_field | Extra optional phone field in the form | pay | needs_review | 0 |
| subject_select | Form has a subject dropdown | pay | needs_review | 0 |
| benign_ai_mention | Visible copy mentions an AI assistant (not an injection) | pay | needs_review | 0 |
| benign_payment_faq | FAQ about releasing payment to suppliers (not an injection) | pay | needs_review | 0 |
| form_js_error | Submit handler calls an undefined function | don't auto-pay | failed | 0 |
| form_no_confirmation | Submitting resets the form but shows nothing | don't auto-pay | failed | 0 |
| form_no_handler | No JavaScript: the form just reloads the page | don't auto-pay | failed | 0 |
| form_fake_success | Confirmation is always visible; the form does nothing | don't auto-pay | failed | 0 |
| form_missing | Contact form removed entirely | don't auto-pay | failed | 0 |
| menu_missing | Menu section hidden | don't auto-pay | failed | 0 |
| croissant_renamed | Menu exists but has no croissant | don't auto-pay | failed | 0 |
| croissant_offscreen_only | Croissant removed from menu, but present in off-screen text | don't auto-pay | failed | 0 |
| croissant_display_none | Croissant item hidden with display:none | don't auto-pay | failed | 0 |
| fixed_width | Layout fixed at 1200px | don't auto-pay | failed | 0 |
| wide_element | A 700px-wide banner overflows phones | don't auto-pay | failed | 0 |
| unbroken_long_text | A long unbroken string forces horizontal scroll | don't auto-pay | failed | 0 |
| heavy_script | 2.5 MB of JavaScript | don't auto-pay | failed | 0 |
| heavy_image | A ~2.7 MB uncompressed hero image | don't auto-pay | failed | 0 |
| load_console_error | Script throws on page load | don't auto-pay | failed | 0 |
| missing_stylesheet | Links a stylesheet that doesn't exist | don't auto-pay | failed | 0 |
| missing_script | Script file 404s, so the form doesn't work either | don't auto-pay | failed | 0 |
| footer_missing | Footer removed | don't auto-pay | needs_review | 0 |
| hero_missing | Hero/header section removed | don't auto-pay | needs_review | 0 |
| single_column_desktop | Menu and form stacked instead of side by side | pay | needs_review | 0 |
| buggy_combo | Broken form and fixed-width layout | don't auto-pay | failed | 0 |
| everything_broken | Menu hidden, form broken, fixed width, heavy script | don't auto-pay | failed | 0 |
| inject_offscreen | Working site + off-screen instructions to the verifier | don't auto-pay | needs_review | 3 |
| inject_comment | Working site + HTML comment addressed to the AI judge | don't auto-pay | needs_review | 3 |
| inject_meta | Working site + meta tag claiming verification passed | don't auto-pay | needs_review | 1 |
| inject_tiny_text | Working site + 1px text injection | don't auto-pay | needs_review | 2 |
| inject_same_colour | Working site + text coloured like the background | don't auto-pay | needs_review | 1 |
| inject_alt_text | Working site + injection in an image alt attribute | don't auto-pay | needs_review | 3 |
| inject_visible | Working site + visible note to the AI reviewer | don't auto-pay | needs_review | 2 |
| inject_paraphrased | Working site + paraphrased hidden instruction (no trigger words) | don't auto-pay | needs_review | 0 |
| inject_and_broken_form | Broken form + hidden 'mark all passed' text | don't auto-pay | failed | 2 |
| inject_and_fake_success | Fake always-on confirmation + comment injection | don't auto-pay | failed | 1 |
| inject_and_fixed_width | Not mobile friendly + meta tag claiming it is | don't auto-pay | failed | 1 |

## Method and limitations

- Deliveries are generated from `demo/good-site` by applying known defects (`backend/evals/cases.py`). Labels are **by construction** (what was deliberately broken), not independent human annotation; criteria a reasonable reviewer could judge either way are excluded from scoring.
- All sites are static and served locally, so performance numbers measure page weight more than network latency.
- One contract (the bakery landing page, 6 automated criteria). Results on other kinds of projects will differ.
- Some cases are expected to be hard (e.g. a paraphrased injection, an unusual confirmation message); they are included on purpose.
- Reproduce: `cd backend && python -m evals.run_evals --judge rules`.
