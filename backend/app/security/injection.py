"""Prompt-injection detection for untrusted submission content.

Deterministic on purpose: cheap, explainable, testable, and not itself injectable (an LLM
classifier reading the attacker's text could be talked out of flagging it).

Detection does not decide pass/fail. Defenses that make injections *ineffective* live
elsewhere (the judge only sees structured check results; untrusted text is fenced; the AI
can't move money). Detection makes attempts *visible*: any flag blocks automatic approval
and puts the attempt in front of a human, in the report.
"""

import re
from enum import StrEnum

from pydantic import BaseModel


class Severity(StrEnum):
    MEDIUM = "medium"  # visible to humans; could be accidental (e.g. a blog post about AI)
    HIGH = "high"  # hidden from humans: no legitimate reason to address an AI there


class SecurityFlag(BaseModel):
    kind: str  # "prompt_injection"
    severity: Severity
    source: str  # hidden_text, html_comment, meta_tag, attribute, visible_text, repo_file
    location: str
    pattern: str
    snippet: str


# Each pattern targets instructions aimed at an automated reviewer, not ordinary copy.
PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,30}\b(previous|prior|above|earlier|all|"
        r"your|system)\b[^.\n]{0,20}\b(instructions?|rules|prompts?|guidelines)\b",
        re.I,
    ),
    "addresses_ai_reviewer": re.compile(
        r"\b(ai|llm|gpt|claude|language model|assistant|bot)[\s_-]*(verifier|judge|reviewer|"
        r"grader|evaluator|checker|agent)\b|\bnote to (the )?(ai|llm|verifier|judge|reviewer)\b",
        re.I,
    ),
    "mark_as_passed": re.compile(
        r"\b(mark|report|rate|score|treat|consider|set)\b[^.\n]{0,40}\b(criteria|criterion|"
        r"checks?|tests?|requirements?|everything|all)\b[^.\n]{0,30}\b(pass(ed|ing)?|approved?|"
        r"compliant|met|satisfied)\b",
        re.I,
    ),
    "release_payment": re.compile(
        r"\b(release|approve|send|transfer)\b[^.\n]{0,20}\b(the )?(payment|funds|money|escrow)\b",
        re.I,
    ),
    "role_hijack": re.compile(
        r"(^|\n|\s)(system|assistant)\s*:|\byou are now\b|\bnew instructions\b|\bjailbreak\b",
        re.I,
    ),
    "fake_preapproval": re.compile(
        r"\b(pre-?approved|already (been )?(reviewed|approved|verified))\b[^.\n]{0,40}\b"
        r"(client|criteria|site|repository|work)\b|\bconfidence\s*(of\s*)?1\.0\b",
        re.I,
    ),
}

HIDDEN_SOURCES = {"hidden_text", "html_comment", "meta_tag", "attribute"}
SNIPPET_CHARS = 160


def detect(text: str, *, source: str, location: str) -> list[SecurityFlag]:
    flags: list[SecurityFlag] = []
    seen: set[str] = set()
    for name, pattern in PATTERNS.items():
        match = pattern.search(text)
        if not match or name in seen:
            continue
        seen.add(name)
        start = max(match.start() - 40, 0)
        start = text.rfind(" ", 0, start) + 1 if start else 0  # don't cut a word in half
        snippet = " ".join(text[start : start + SNIPPET_CHARS].split())
        flags.append(
            SecurityFlag(
                kind="prompt_injection",
                severity=Severity.HIGH if source in HIDDEN_SOURCES else Severity.MEDIUM,
                source=source,
                location=location,
                pattern=name,
                snippet=snippet,
            )
        )
    # One weak signal in visible copy (e.g. "release the funds" in an FAQ) isn't enough.
    weak_alone = {"release_payment", "addresses_ai_reviewer"}
    if source not in HIDDEN_SOURCES and len(flags) == 1 and flags[0].pattern in weak_alone:
        return []
    return flags


def dedupe(flags: list[SecurityFlag]) -> list[SecurityFlag]:
    unique: dict[tuple[str, str, str], SecurityFlag] = {}
    for flag in flags:
        unique.setdefault((flag.source, flag.location, flag.pattern), flag)
    return list(unique.values())
