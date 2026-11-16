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
    ("assigned secret", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|client[_-]?secret)\b\s*[:=]\s*"
        r"[\"']?(?!\$|\{|<|\*{3}|x{3}|your|changeme|example|none|null|true|false)([^\s\"'`]{8,})")),
]

ALLOW = re.compile(r"brain:allow-secret")


def scan(text: str) -> list[tuple[str, str]]:
    """Returns (kind, redacted sample) for each finding."""
    found = []
    for line in text.splitlines():
        if ALLOW.search(line):
            continue
        for kind, rx in PATTERNS:
            m = rx.search(line)
            if m:
                val = m.group(0)
                found.append((kind, val[:6] + "…" if len(val) > 6 else "…"))
    return found
