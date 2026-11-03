"""Run every live-site check against a demo site (or any URL) and print the results.

cd backend
python -m scripts.check_site good-site
python -m scripts.check_site buggy-site
python -m scripts.check_site --url https://example.com --repo https://github.com/o/r
"""

import argparse
import asyncio
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.llm import build_llm
from app.core.logging import configure_logging
from app.verification.models import CheckResult
from app.verification.repo_check import repo_check
from app.verification.visual_match import visual_match
from app.verification.web_checks import LLMSelectorSuggester, WebChecker

DEMO_DIR = Path(__file__).resolve().parents[2] / "demo"
MOCKUP_HTML = DEMO_DIR / "mockup" / "mockup.html"
MOCKUP_PNG = DEMO_DIR / "mockup" / "mockup.png"
ICONS = {True: "PASS", False: "FAIL", None: "HUMAN"}


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


def serve_demo() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=DEMO_DIR))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def show(result: CheckResult) -> None:
    print(f"\n[{ICONS[result.passed]:5}] {result.check_type}: {result.summary}")
    for key, value in result.evidence.measurements.items():
        print(f"         {key} = {value}")
    for line in result.evidence.logs[:6]:
        print(f"         > {line}")
    if result.error:
        print(f"         error: {result.error}")


async def run(url: str, repo: str | None, base: str | None) -> None:
    settings = get_settings()
    llm = build_llm(settings)
    locator = LLMSelectorSuggester(llm) if llm else None
    artifacts = settings.artifacts_dir / "check_site"

    async with WebChecker(artifacts, locator=locator) as web:
        if base and not MOCKUP_PNG.exists():
            MOCKUP_PNG.write_bytes(await web.screenshot(f"{base}/mockup/mockup.html"))

        print(f"Checking {url}")
        show(await web.page_loads("C1", url))
        show(await web.element_present("C2", url, text="Croissant", description="Menu"))
        show(await web.form_submits("C3", url))
        show(await web.responsive_layout("C4", url, viewports=[375, 1440]))
        show(await web.performance("C5", url, max_load_seconds=3, max_page_weight_kb=2000))

        if llm and MOCKUP_PNG.exists():
            shot = await web.screenshot(url)
            shot_path = artifacts / "C6_live.png"
            shot_path.write_bytes(shot)
            show(
                await visual_match(
                    llm,
                    criterion_id="C6",
                    mockup=MOCKUP_PNG.read_bytes(),
                    screenshot=shot,
                    min_match_score=0.7,
                    screenshot_path=shot_path,
                )
            )
    if repo:
        show(await repo_check("C7", repo, required_files=["README.md"]))
    print(f"\nScreenshots: {artifacts.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", nargs="?", help="good-site | buggy-site | malicious-site")
    parser.add_argument("--url", help="Check any live URL instead of a demo site")
    parser.add_argument("--repo", help="GitHub repo URL for repo_check")
    args = parser.parse_args()
    configure_logging("WARNING")

    base = None
    if args.url:
        url = args.url
    else:
        base = serve_demo()
        url = f"{base}/{args.demo or 'good-site'}/"
    asyncio.run(run(url, args.repo, base))


if __name__ == "__main__":
    main()
