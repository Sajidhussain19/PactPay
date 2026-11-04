"""Security scan of a submission, run in parallel with the acceptance checks."""

import asyncio
import logging
import re
from pathlib import Path

from app.security.injection import SecurityFlag, dedupe, detect
from app.verification.repo_check import RepoError, cloned_repo
from app.verification.report import Submission
from app.verification.web_checks import UntrustedContent, WebChecker

logger = logging.getLogger(__name__)

# Only human-facing docs are scanned. Code legitimately contains things like `system:` keys
# or prompts (if the deliverable is itself an AI app), which would be noisy false positives.
DOC_SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm", ".rst"}
MAX_FILES = 200
MAX_FILE_BYTES = 200_000
HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.S)


def scan_page(content: UntrustedContent, url: str) -> list[SecurityFlag]:
    flags: list[SecurityFlag] = []
    for text in content.hidden:
        flags += detect(text, source="hidden_text", location=url)
    for text in content.comments:
        flags += detect(text, source="html_comment", location=url)
    for text in content.metas:
        flags += detect(text, source="meta_tag", location=url)
    for text in content.attrs:
        flags += detect(text, source="attribute", location=url)
    flags += detect(content.visible, source="visible_text", location=url)
    return flags


def scan_repo_docs(root: Path) -> list[SecurityFlag]:
    flags: list[SecurityFlag] = []
    docs = [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and ".git" not in p.relative_to(root).parts
        and (p.suffix.lower() in DOC_SUFFIXES or p.name.lower().startswith("readme"))
    ][:MAX_FILES]
    for path in docs:
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        location = path.relative_to(root).as_posix()
        # Comments are invisible when Markdown/HTML is rendered: hidden from the human reviewer.
        for comment in HTML_COMMENT.findall(text):
            flags += detect(comment, source="html_comment", location=location)
        flags += detect(HTML_COMMENT.sub(" ", text), source="repo_file", location=location)
    return flags


class SubmissionScanner:
    def __init__(
        self,
        web: WebChecker | None,
        *,
        scan_repo: bool = True,
        repo_max_size_mb: int = 50,
        repo_timeout_seconds: float = 60,
    ) -> None:
        self._web = web
        self._scan_repo = scan_repo
        self._repo_max_size_mb = repo_max_size_mb
        self._repo_timeout_seconds = repo_timeout_seconds

    async def __call__(self, submission: Submission) -> list[SecurityFlag]:
        flags: list[SecurityFlag] = []
        if self._web is not None:
            try:
                content = await self._web.untrusted_content(submission.live_url)
                flags += scan_page(content, submission.live_url)
            except Exception as exc:  # the acceptance checks report load failures
                logger.warning("Security page scan skipped: %s", exc)
        if self._scan_repo and submission.repo_url:
            try:
                async with cloned_repo(
                    submission.repo_url,
                    max_size_mb=self._repo_max_size_mb,
                    timeout_seconds=self._repo_timeout_seconds,
                ) as root:
                    flags += await asyncio.to_thread(scan_repo_docs, root)
            except RepoError as exc:
                logger.warning("Security repo scan skipped: %s", exc)
        return dedupe(flags)
