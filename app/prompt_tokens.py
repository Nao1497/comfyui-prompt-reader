"""Prompt normalisation and word extraction (design.md §6 "正規化", FR-46).

The same ``normalize`` is applied to dictionary names so that a prompt word and
a danbooru tag compare equal only when they are the same tag.
"""

from __future__ import annotations

import re

# Escaped brackets are literal characters of a tag (``hatsune_miku_\(cosplay\)``).
_ESCAPES = {"\\(": "\x00", "\\)": "\x01", "\\[": "\x02", "\\]": "\x03", "\\{": "\x04", "\\}": "\x05"}
_UNESCAPES = {"\x00": "(", "\x01": ")", "\x02": "[", "\x03": "]", "\x04": "{", "\x05": "}"}

# Innermost emphasis / weight groups: (text), (text:1.2), [text], {text}, [text:0.5]
_GROUP_RE = re.compile(r"[(\[{]([^()\[\]{}]*)[)\]}]")
_WEIGHT_RE = re.compile(r"^(.*?):\s*[-+]?\d*\.?\d+\s*$", re.S)
_ANGLE_RE = re.compile(r"<[^<>]*>")  # <lora:name:0.8>, <hypernet:...>
_BREAK_RE = re.compile(r"\bBREAK\b")
_WS_RE = re.compile(r"\s+")


def _strip_groups(fragment: str) -> str:
    """Remove emphasis brackets and weights, keeping the wrapped text."""
    prev = None
    while prev != fragment:
        prev = fragment

        def repl(m: re.Match) -> str:
            inner = m.group(1)
            w = _WEIGHT_RE.match(inner)
            return w.group(1) if w else inner

        fragment = _GROUP_RE.sub(repl, fragment)
    # Stray unbalanced brackets are noise, not tag text.
    return re.sub(r"[()\[\]{}]", "", fragment)


def normalize_name(name: str) -> str:
    """Normalise a dictionary tag name: keep its brackets, only unify separators and case.

    ``hatsune_miku_(cosplay)`` -> ``hatsune miku (cosplay)``, which is what the
    prompt form ``hatsune miku \\(cosplay\\)`` normalises to via :func:`normalize`.
    """
    if not name:
        return ""
    return _WS_RE.sub(" ", name.replace("_", " ").lower()).strip()


def normalize(text: str) -> str:
    """Normalise one prompt fragment per FR-46. Returns '' for nothing."""
    if not text:
        return ""
    s = text
    for esc, ph in _ESCAPES.items():
        s = s.replace(esc, ph)
    s = _ANGLE_RE.sub(" ", s)
    s = _strip_groups(s)
    for ph, ch in _UNESCAPES.items():
        s = s.replace(ph, ch)
    s = s.replace("_", " ")
    s = s.lower()
    # Runs of whitespace (including newlines inside a fragment) become one space.
    s = _WS_RE.sub(" ", s).strip()
    return s


def tokenize(prompt: str | None) -> list[str]:
    """Split a prompt on commas (and BREAK) and normalise each fragment, in order."""
    if not prompt:
        return []
    without_angles = _ANGLE_RE.sub(" ", prompt)
    fragments = _BREAK_RE.sub(",", without_angles).split(",")
    tokens: list[str] = []
    for frag in fragments:
        if frag.strip().lower().startswith("embedding:"):
            continue
        tok = normalize(frag)
        if tok:
            tokens.append(tok)
    return tokens
