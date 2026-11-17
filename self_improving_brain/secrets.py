"""Secret scan. Runs before anything is written to the vault and as a git pre-commit hook.

A brain that syncs to git must never learn a password. Names of environment variables are
fine (`API_KEY=$API_KEY`), values are not.
"""
from __future__ import annotations

import re

PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{30,}\b")),
    ("gitlab token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("openai/anthropic key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{24,}\b")),
    ("pypi token", re.compile(r"\bpypi-AgE[A-Za-z0-9_\-]{50,}")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("secret flag", re.compile(r"(?i)--(?:password|passwd|token|api-key|secret)(?:=|\s+)(?!\$|<|\*{3})[^\s\"']{8,}")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe key", re.compile(r"\b(?:sk|rk)_live_[0-9a-zA-Z]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("url with password", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s@/]{4,}@[^\s]+", re.I)),
    ("bearer token", re.compile(r"(?i)\bbearer\s+(?=[A-Za-z_\-.=/+]*[0-9])[A-Za-z0-9_\-.=/+]{20,}")),
    ("sendgrid key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{20,}")),
    # the name may sit inside a longer one (GITHUB_TOKEN, db_password, aws_secret_access_key) and be quoted (JSON)
    ("assigned secret", re.compile(
        r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z0-9]+_)*(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|client[_\-]?secret)"
        r"(?:_[A-Za-z0-9]+)*[\"']?\s*[:=]\s*"
        r"[\"']?(?!\$|\{|<|\[\[|\*{3}|x{3}|your|changeme|example|none|null|true|false|os\.|process\.env|getenv|env\b|ENV\[|secrets\.)"
        r"(?![0-9]+\b)([^\s\"'`,;)]{8,})")),
]

ALLOW = re.compile(r"brain:allow-secret")


def scan(text: str, allow_marker: bool = False) -> list[tuple[str, str]]:
    """Returns (kind, redacted sample) for each finding.

    `brain:allow-secret` on a line is the user's own word that a value is not a secret. Only the user's own
    paths honour it (the pre-commit scan, lint of notes they wrote): an agent or a model could write the
    marker too, so the hook, the inbox and every check of model output pass allow_marker=False."""
    found = []
    for line in text.splitlines():
        if allow_marker and ALLOW.search(line):
            continue
        for kind, rx in PATTERNS:
            m = rx.search(line)
            if m:
                val = m.group(0)
                found.append((kind, val[:6] + "…" if len(val) > 6 else "…"))
    return found


def redact(text: str) -> str:
    """The same patterns, replaced: what leaves the machine (a prompt to the LLM CLI) or lands in the brain's
    logs never carries a secret value."""
    for kind, rx in PATTERNS:
        text = rx.sub(f"[redacted {kind}]", text)
    return text
