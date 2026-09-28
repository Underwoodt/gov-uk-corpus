"""The AI prompts — defined in code, versioned by git.

Two prompts drive the AI features: the inclusion + exclusion evaluation templates
(govuk_corpus/evaluate.py). Git is the single source of truth: a prompt change is a code
commit, and `template_version()` (the git commit) is what a run stamps as its template
version. Callers resolve the current text with `default_text` and pass it in (evaluate
stays DB-free).

(An earlier in-app "save a new version" mechanism backed by a `prompt_versions` table was
retired and the table dropped: it could silently override git, and no version was ever saved.
Runs never depended on it for reproducibility — each run stamps its full template in `prompt_spec`.)
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from typing import List

from . import evaluate

NAMES = ("inclusion", "exclusion")

LABELS = {
    "inclusion": "AI evaluation — Phase 1 (Inclusion)",
    "exclusion": "AI evaluation — Phase 2 (Exclusion)",
}

# The {{PLACEHOLDER}} tokens each template fills at run time — shown on the Settings page so a
# reader knows which values the template carries.
PLACEHOLDERS = {
    "inclusion": "{{INCLUDE}}, {{TITLE}}, {{DESCRIPTION}}, {{BODY}}",
    "exclusion": ("{{NAME}}, {{NAME_UPPER}}, {{INCLUDE}}, {{EXCLUDE}}, {{KEEP}}, {{DROP}}, "
                  "{{PASS1_REASON}}, {{PASS1_TOPIC}}, {{TITLE}}, {{DESCRIPTION}}, {{BODY}}"),
}

_DEFAULTS = {
    "inclusion": lambda: evaluate.DEFAULT_INCLUSION_TEMPLATE,
    "exclusion": lambda: evaluate.DEFAULT_EXCLUSION_TEMPLATE,
}

# Tokens a template MUST keep, or the built prompt loses that value at run time. Both
# evaluation templates carry per-page values.
REQUIRED_PLACEHOLDERS = {
    "inclusion": ("INCLUDE", "TITLE", "DESCRIPTION", "BODY"),
    "exclusion": ("NAME", "NAME_UPPER", "INCLUDE", "EXCLUDE", "KEEP", "DROP",
                  "PASS1_REASON", "PASS1_TOPIC", "TITLE", "BODY"),
}

# The repo root (parent of this package) — where `git` answers for the deployed checkout.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def missing_placeholders(name: str, body: str) -> List[str]:
    """Required {{TOKEN}}s that `body` is missing (as they'd appear in the template, e.g. '{{BODY}}')."""
    body = body or ""
    return ["{{" + t + "}}" for t in REQUIRED_PLACEHOLDERS.get(name, ())
            if ("{{" + t + "}}") not in body]


def default_text(name: str) -> str:
    """The prompt text for `name` — the code (git) default, which is the only version there is."""
    return _DEFAULTS[name]()


@lru_cache(maxsize=1)
def template_version() -> str:
    """The git commit the prompts come from (short SHA): the prompts' version, stamped on each
    run's prompt_spec and shown on Settings. Tries `git rev-parse`, then reads .git/HEAD directly
    (no git binary needed), then falls back to 'git'. Cached for the process — a deploy restarts it."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT,
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    try:
        with open(os.path.join(_REPO_ROOT, ".git", "HEAD"), encoding="utf-8") as fh:
            ref = fh.read().strip()
        if ref.startswith("ref:"):
            with open(os.path.join(_REPO_ROOT, ".git", ref.split(" ", 1)[1].strip()),
                      encoding="utf-8") as fh:
                ref = fh.read().strip()
        return ref[:12] if ref else "git"
    except Exception:
        return "git"
