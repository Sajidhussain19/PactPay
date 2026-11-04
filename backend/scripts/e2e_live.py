"""Live end-to-end run through the HTTP API: real PayPal sandbox, real browser checks, real
AI judge. Plays both the client and the freelancer.

    # terminal 1
    cd backend && uvicorn app.main:app --port 8010
    # terminal 2
    cd backend && python -m scripts.e2e_live

Flow: contract -> both approve -> fund (sandbox card) -> submit buggy site (FAILS)
-> submit good site (PASSES) -> client approves -> capture + payout -> RELEASED.
"""

import argparse
import base64
import json
import time
from typing import Any

import httpx

from scripts.check_site import DEMO_DIR, MOCKUP_PNG, serve_demo


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def login(api: httpx.Client, role: str) -> dict[str, str]:
    token = api.post("/auth/demo-login", json={"role": role}).raise_for_status().json()["token"]
    return {"Authorization": f"Bearer {token}"}


def ok(response: httpx.Response) -> Any:
    if response.is_error:
        request = response.request
        raise SystemExit(
            f"{request.method} {request.url} -> {response.status_code}: {response.text}"
        )
    return response.json()


def follow_stream(api: httpx.Client, milestone_id: str, headers: dict[str, str]) -> None:
    with api.stream(
        "GET", f"/milestones/{milestone_id}/verification/stream", headers=headers, timeout=180
    ) as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event["type"] == "judged":
                summary = event.get("summary", "")[:80]
                print(f"  {event['criterion_id']:6} {event['status']:12} {summary}")
            elif event["type"] == "report_ready":
                print(f"  -> report ready: {event.get('status')}")


def submit(api: httpx.Client, mid: str, headers: dict[str, str], url: str) -> dict[str, Any]:
    ok(api.post(f"/milestones/{mid}/submissions", json={"live_url": url}, headers=headers))
    follow_stream(api, mid, headers)
    return ok(api.get(f"/milestones/{mid}", headers=headers))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    args = parser.parse_args()

    demo = serve_demo()
    api = httpx.Client(base_url=args.api, timeout=120)
    client, freelancer = login(api, "client"), login(api, "freelancer")

    step("1. Client creates the bakery contract (with mockup)")
    contract_json = json.loads((DEMO_DIR / "contracts" / "bakery.json").read_text())
    body = {
        "contract": contract_json,
        "freelancer_email": "freelancer@pactpay.demo",
        "mockup_base64": base64.b64encode(MOCKUP_PNG.read_bytes()).decode(),
    }
    contract = ok(api.post("/contracts", json=body, headers=client))
    print(f"contract {contract['id']}: {contract['title']} ({contract['total']} USD)")

    step("2. Both parties approve the acceptance criteria")
    ok(api.post(f"/contracts/{contract['id']}/approve", headers=client))
    contract = ok(api.post(f"/contracts/{contract['id']}/approve", headers=freelancer))
    mid = contract["milestones"][0]["id"]
    print(f"milestone {mid}: {contract['milestones'][0]['state']}")

    step("3. Client funds: PayPal AUTHORIZES $150 (held, not taken)")
    funded = ok(
        api.post(f"/milestones/{mid}/fund", json={"method": "sandbox_card"}, headers=client)
    )
    m = funded["milestone"]
    print(f"state={m['state']} authorization={m['paypal_authorization_id']}")

    step("4. Freelancer submits the BUGGY site")
    m = submit(api, mid, freelancer, f"{demo}/buggy-site/")
    print(f"state={m['state']}")
    for c in m["latest_run"]["report"]["criteria"]:
        if c.get("fix_suggestion"):
            print(f"  FIX {c['criterion_id']}: {c['fix_suggestion']}")

    step("5. Freelancer fixes it and submits the GOOD site")
    m = submit(api, mid, freelancer, f"{demo}/good-site/")
    print(f"state={m['state']} counts={m['latest_run']['report']['counts']}")
    if m["state"] != "passed":
        raise SystemExit("Expected PASSED; stopping before any money moves.")

    step("6. Client approves release: capture + payout")
    m = ok(api.post(f"/milestones/{mid}/approve-release", headers=client))
    print(
        f"state={m['state']} capture={m['paypal_capture_id']} payout_batch={m['payout_batch_id']}"
    )
    for _ in range(30):
        if m["state"] == "released":
            break
        time.sleep(5)
        m = ok(api.get(f"/milestones/{mid}", headers=client))
        print(f"  payout_status={m['payout_status']} state={m['state']}")

    step("RESULT")
    print(
        f"state={m['state']}  freelancer received {m['freelancer_receives']} {m['currency']}"
        f" (fee {m['platform_fee']})"
    )


if __name__ == "__main__":
    main()
