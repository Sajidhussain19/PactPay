"""Static checks on the freelancer's GitHub repository.

Safety: the repo is untrusted. We only accept https://github.com/<owner>/<repo>, check its
size before cloning, clone shallowly with a timeout, never execute anything from it, disable
symlinks (so a link can't point at our filesystem) and skip Git LFS downloads.
"""

import asyncio
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.agents.contract_schema import CheckType
from app.verification.models import CheckResult, Evidence

GITHUB_URL = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
MAX_SCAN_FILE_BYTES = 1_000_000
MAX_FINDINGS = 20

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Stripe live key": re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "Hardcoded credential": re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token)\b\s*[:=]\s*"
        r"['\"][^'\"\s]{12,}['\"]"
    ),
}
README = re.compile(r"^readme(\.[a-z]+)?$", re.I)
LICENSE = re.compile(r"^(licen[sc]e|copying)(\.[a-z]+)?$", re.I)
STACK_MARKERS = {
    "package.json": "Node.js",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "index.html": "Static site",
    "Gemfile": "Ruby",
    "go.mod": "Go",
    "composer.json": "PHP",
}


class RepoError(Exception):
    """The repository URL is invalid, unreachable, too large, or cloning failed."""


class SecretFinding(BaseModel):
    kind: str
    location: str
    preview: str  # redacted


class RepoReport(BaseModel):
    file_count: int
    total_kb: int
    top_level: list[str]
    languages: dict[str, int]
    stack: list[str]
    has_readme: bool
    has_license: bool
    missing_files: list[str]
    secrets: list[SecretFinding]


def parse_github_url(url: str) -> tuple[str, str]:
    match = GITHUB_URL.match(url.strip())
    if not match:
        raise RepoError("Repository must be a public https://github.com/<owner>/<repo> URL")
    return match["owner"], match["repo"]


@asynccontextmanager
async def cloned_repo(
    url: str, *, max_size_mb: int = 50, timeout_seconds: float = 60
) -> AsyncIterator[Path]:
    """Clone into a temp dir that is deleted afterwards."""
    owner, repo = parse_github_url(url)
    await _check_remote_size(owner, repo, max_size_mb)
    workdir = Path(tempfile.mkdtemp(prefix="pactpay-repo-"))
    target = workdir / "repo"
    try:
        await _git_clone(f"https://github.com/{owner}/{repo}.git", target, timeout_seconds)
        size_mb = _dir_size(target) / 1024 / 1024
        if size_mb > max_size_mb:
            raise RepoError(f"Repository is {size_mb:.0f} MB, limit is {max_size_mb} MB")
        yield target
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _check_remote_size(owner: str, repo: str, max_size_mb: int) -> None:
    async with httpx.AsyncClient(timeout=10) as http:
        try:
            response = await http.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                headers={"Accept": "application/vnd.github+json"},
            )
        except httpx.HTTPError:
            return  # GitHub API unreachable; the post-clone size check still applies
    if response.status_code == 404:
        raise RepoError(f"Repository {owner}/{repo} not found or not public")
    if response.status_code == 200:
        size_mb = response.json().get("size", 0) / 1024  # API reports KB
        if size_mb > max_size_mb:
            raise RepoError(f"Repository is {size_mb:.0f} MB, limit is {max_size_mb} MB")


async def _git_clone(clone_url: str, target: Path, timeout_seconds: float) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "core.symlinks=false",
        "-c",
        "protocol.file.allow=never",
        "clone",
        "--depth=1",
        "--single-branch",
        "--no-tags",
        "--quiet",
        clone_url,
        str(target),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RepoError(f"Cloning took longer than {timeout_seconds:.0f}s") from exc
    if process.returncode != 0:
        raise RepoError(f"git clone failed: {stderr.decode(errors='replace').strip()[:300]}")


def inspect_repository(root: Path, *, required_files: list[str] | None = None) -> RepoReport:
    files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts]
    relative = {p.relative_to(root).as_posix().lower() for p in files}
    top_names = sorted(p.name for p in root.iterdir() if p.name != ".git")
    extensions = Counter(p.suffix.lower() for p in files if p.suffix)

    secrets: list[SecretFinding] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if path.name == ".env" or (path.name.startswith(".env.") and "example" not in path.name):
            secrets.append(
                SecretFinding(kind="Committed .env file", location=rel, preview="(file)")
            )
        secrets.extend(_scan_file(path, rel))
        if len(secrets) >= MAX_FINDINGS:
            break

    return RepoReport(
        file_count=len(files),
        total_kb=round(sum(p.stat().st_size for p in files) / 1024),
        top_level=top_names[:30],
        languages=dict(extensions.most_common(8)),
        stack=sorted({STACK_MARKERS[n] for n in top_names if n in STACK_MARKERS}),
        has_readme=any(README.match(n) for n in top_names),
        has_license=any(LICENSE.match(n) for n in top_names),
        missing_files=[f for f in required_files or [] if f.lower().lstrip("/") not in relative],
        secrets=secrets[:MAX_FINDINGS],
    )


def report_to_result(criterion_id: str, report: RepoReport, duration_ms: int = 0) -> CheckResult:
    problems = []
    if not report.has_readme:
        problems.append("no README")
    if report.missing_files:
        problems.append(f"missing {', '.join(report.missing_files)}")
    if report.secrets:
        problems.append(f"{len(report.secrets)} possible secret(s) committed")

    evidence = Evidence(
        logs=[f"{s.kind} in {s.location}: {s.preview}" for s in report.secrets]
        + [f"Top level: {', '.join(report.top_level)}"],
        measurements={
            "files": report.file_count,
            "size_kb": report.total_kb,
            "has_readme": report.has_readme,
            "has_license": report.has_license,
            "stack": ", ".join(report.stack) or "unknown",
        },
    )
    summary = (
        "Repository checks failed: " + "; ".join(problems)
        if problems
        else f"README present, {report.file_count} files, no secrets found"
        + ("" if report.has_license else " (no LICENSE file)")
    )
    return CheckResult(
        criterion_id=criterion_id,
        check_type=CheckType.REPO_CHECK,
        passed=not problems,
        summary=summary,
        evidence=evidence,
        duration_ms=duration_ms,
    )


async def repo_check(
    criterion_id: str,
    repo_url: str,
    *,
    required_files: list[str] | None = None,
    max_size_mb: int = 50,
    timeout_seconds: float = 60,
) -> CheckResult:
    started = time.monotonic()
    try:
        async with cloned_repo(
            repo_url, max_size_mb=max_size_mb, timeout_seconds=timeout_seconds
        ) as root:
            report = await asyncio.to_thread(
                inspect_repository, root, required_files=required_files
            )
    except RepoError as exc:
        return CheckResult(
            criterion_id=criterion_id,
            check_type=CheckType.REPO_CHECK,
            passed=False,
            summary="Repository could not be checked",
            error=str(exc),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    return report_to_result(criterion_id, report, int((time.monotonic() - started) * 1000))


def _scan_file(path: Path, rel: str) -> list[SecretFinding]:
    try:
        if path.stat().st_size > MAX_SCAN_FILE_BYTES:
            return []
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\0" in raw[:8192]:
        return []  # binary
    findings = []
    for line_no, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
        for kind, pattern in SECRET_PATTERNS.items():
            if match := pattern.search(line):
                findings.append(
                    SecretFinding(kind=kind, location=f"{rel}:{line_no}", preview=_redact(match[0]))
                )
    return findings


def _redact(secret: str) -> str:
    return secret[:6] + "…" if len(secret) > 6 else "…"


def _dir_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
