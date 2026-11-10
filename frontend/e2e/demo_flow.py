"""Click through the judge demo in a real browser and save screenshots.

Needs the backend on :8010 and the frontend on :3000 (see README). Uses the real PayPal
sandbox and the real AI verifier, so it takes a few minutes.

    cd backend
    .venv/Scripts/python ../frontend/e2e/demo_flow.py            # fail -> fix -> pass -> paid
    .venv/Scripts/python ../frontend/e2e/demo_flow.py --sneaky   # manipulation attempt
"""

import argparse
import re
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

APP = "http://localhost:3000"
SHOTS = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
VERIFY_TIMEOUT = 240_000


def shot(page: Page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
    print(f"  saved {name}.png")


def switch_role(page: Page, label: str) -> None:
    page.get_by_role("button", name=f"Switch to {label}").click()
    page.wait_for_timeout(1500)


def submit(page: Page, site_label: str) -> None:
    page.get_by_role("button", name=site_label, exact=True).click()
    page.get_by_role("button", name="Submit for verification").click()
    expect(page.get_by_text("Live verification")).to_be_visible(timeout=30_000)


def wait_for_report(page: Page) -> None:
    expect(page.get_by_text("Verification report")).to_be_visible(timeout=VERIFY_TIMEOUT)
    page.wait_for_timeout(1000)


def fund_bakery(page: Page) -> None:
    page.goto(APP)
    page.get_by_role("button", name="Reset demo").click()
    expect(page.get_by_text("Demo reset")).to_be_visible(timeout=30_000)
    page.get_by_role("button", name=re.compile("Continue as the client")).click()
    expect(page.get_by_text("Held by PayPal").first).to_be_visible(timeout=30_000)
    page.locator(".ag-row", has_text="Landing page delivered").first.click()
    expect(page.get_by_text("Fund this milestone")).to_be_visible(timeout=30_000)
    page.get_by_role("button", name=re.compile("Instant demo funding")).click()
    expect(page.get_by_text("held by PayPal, not taken")).to_be_visible(timeout=60_000)


def main_flow(page: Page) -> None:
    page.goto(APP)
    shot(page, "01-landing")

    print("Client funds the milestone")
    fund_bakery(page)
    page.goto(f"{APP}/dashboard")
    expect(page.get_by_text("Landing page delivered").first).to_be_visible()
    shot(page, "02-dashboard-client")
    page.locator(".ag-row", has_text="Landing page delivered").first.click()
    expect(page.get_by_text("held by PayPal, not taken")).to_be_visible(timeout=30_000)
    shot(page, "03-funded")

    print("Freelancer submits the buggy site")
    switch_role(page, "Freelancer")
    submit(page, "Buggy site")
    page.wait_for_timeout(6000)
    shot(page, "04-live-verification")
    wait_for_report(page)
    expect(page.get_by_text("What to fix before resubmitting")).to_be_visible()
    shot(page, "05-failed-with-fixes")

    print("Freelancer fixes it")
    submit(page, "Fixed site")
    wait_for_report(page)
    expect(page.get_by_text("All automated checks passed")).to_be_visible()
    shot(page, "06-passed")

    print("Client approves: capture + payout")
    switch_role(page, "Client")
    page.get_by_role("button", name="Approve and release payment").click()
    expect(page.get_by_text(re.compile(r"paid to the freelancer"))).to_be_visible(timeout=180_000)
    shot(page, "07-paid")

    switch_role(page, "Freelancer")
    expect(page.get_by_text(re.compile(r"paid to you"))).to_be_visible(timeout=30_000)
    shot(page, "08-freelancer-paid")


def sneaky_flow(page: Page) -> None:
    print("Sneaky delivery: works, but tries to trick the AI")
    fund_bakery(page)
    switch_role(page, "Freelancer")
    submit(page, "Sneaky site")
    wait_for_report(page)
    expect(page.get_by_text("Manipulation attempt detected").first).to_be_visible()
    shot(page, "09-manipulation-caught")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sneaky", action="store_true")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        (sneaky_flow if args.sneaky else main_flow)(page)
        browser.close()
    print("Demo flow OK")


if __name__ == "__main__":
    main()
