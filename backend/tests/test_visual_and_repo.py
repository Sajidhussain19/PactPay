from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.llm import LLMError
from app.verification.repo_check import (
    RepoError,
    inspect_repository,
    parse_github_url,
    report_to_result,
)
from app.verification.visual_match import (
    SectionMatch,
    VisualComparison,
    score_sections,
    visual_match,
)


class FakeVisionLLM:
    def __init__(self, result: VisualComparison | Exception) -> None:
        self.result = result
        self.images: list[bytes] = []

    async def parse(
        self, *, system: str, user: str, schema: type[BaseModel], images: Sequence[bytes] = ()
    ) -> Any:
        self.images = list(images)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def comparison(*statuses: str) -> VisualComparison:
    return VisualComparison(
        sections=[
            SectionMatch(section=f"section {i}", status=s, notes="n")  # type: ignore[arg-type]
            for i, s in enumerate(statuses)
        ],
        summary="s",
    )


# --------------------------------------------------------------- visual_match


def test_score_is_computed_from_sections() -> None:
    assert score_sections(comparison("match", "partial", "missing", "match").sections) == 0.62
    assert score_sections([]) == 0.0


async def test_visual_match_passes_above_threshold() -> None:
    llm = FakeVisionLLM(comparison("match", "match", "partial"))

    result = await visual_match(
        llm, criterion_id="V1", mockup=b"mock", screenshot=b"shot", min_match_score=0.7
    )

    assert result.passed is True
    assert result.evidence.measurements["match_score"] == 0.83
    assert llm.images == [b"mock", b"shot"]  # mockup first, then live site


async def test_visual_match_fails_and_names_missing_sections() -> None:
    llm = FakeVisionLLM(comparison("match", "missing", "missing"))

    result = await visual_match(
        llm, criterion_id="V1", mockup=b"m", screenshot=b"s", min_match_score=0.7
    )

    assert result.passed is False
    assert "missing: section 1, section 2" in result.summary


async def test_any_missing_section_fails_even_above_threshold() -> None:
    llm = FakeVisionLLM(comparison("match", "match", "match", "match", "missing"))  # 80%

    result = await visual_match(
        llm, criterion_id="V1", mockup=b"m", screenshot=b"s", min_match_score=0.7
    )

    assert result.evidence.measurements["match_score"] == 0.8
    assert result.passed is False
    assert "missing: section 4" in result.summary


async def test_visual_match_llm_failure_needs_human() -> None:
    llm = FakeVisionLLM(LLMError("timeout"))

    result = await visual_match(
        llm, criterion_id="V1", mockup=b"m", screenshot=b"s", min_match_score=0.7
    )

    assert result.passed is None


# ----------------------------------------------------------------- repo_check


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/octocat/Hello-World", ("octocat", "Hello-World")),
        ("https://github.com/octocat/Hello-World.git", ("octocat", "Hello-World")),
        ("https://github.com/a-b/c.d_e/", ("a-b", "c.d_e")),
    ],
)
def test_parse_github_url(url: str, expected: tuple[str, str]) -> None:
    assert parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "https://github.com/a",
        "https://github.com/a/b/tree/main",
        "file:///etc/passwd",
        "https://github.com.evil.com/a/b",
    ],
)
def test_parse_github_url_rejects_others(url: str) -> None:
    with pytest.raises(RepoError):
        parse_github_url(url)


def make_repo(root: Path, files: dict[str, str | bytes]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
    return root


def test_clean_repo_passes(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        {"README.md": "# Site", "LICENSE": "MIT", "index.html": "<h1>Hi</h1>", "css/a.css": ""},
    )

    report = inspect_repository(repo, required_files=["index.html"])
    result = report_to_result("R1", report)

    assert result.passed is True
    assert report.stack == ["Static site"]
    assert report.has_license


def test_missing_readme_and_required_file_fail(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"index.html": ""})

    result = report_to_result("R1", inspect_repository(repo, required_files=["contact.html"]))

    assert result.passed is False
    assert "no README" in result.summary
    assert "contact.html" in result.summary


def test_committed_secrets_are_found_and_redacted(tmp_path: Path) -> None:
    openai_key = "sk-proj-" + "a1B2c3D4" * 5
    repo = make_repo(
        tmp_path,
        {
            "README.md": "# x",
            "config.js": f'const apiKey = "{openai_key}";',
            ".env": "PAYPAL_SECRET=abc",
            ".env.example": "PAYPAL_SECRET=",
            "img.png": b"\x89PNG\0\0sk-proj-" + b"a" * 40,
        },
    )

    report = inspect_repository(repo)
    result = report_to_result("R1", report)

    kinds = {s.kind for s in report.secrets}
    assert {"OpenAI API key", "Committed .env file"} <= kinds
    assert all(".env.example" not in s.location for s in report.secrets)
    assert all("img.png" not in s.location for s in report.secrets)  # binaries skipped
    assert openai_key not in result.model_dump_json()  # never echo the secret
    assert result.passed is False


def test_git_directory_is_ignored(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"README.md": "# x", ".git/config": 'password = "hunter2hunter2"'})

    assert inspect_repository(repo).secrets == []
