"""Task normalisation and duplicate matching (SDD §6.5)."""
from __future__ import annotations

import re

STOP_WORDS = {"the", "a", "an", "to", "for", "with", "of", "on", "our", "their", "and", "about", "in", "into"}
SPELLING = {
    "finalize": "finalise", "finalized": "finalised", "color": "colour", "colors": "colours",
    "organize": "organise", "prioritize": "prioritise", "summarize": "summarise", "optimize": "optimise",
}
THRESHOLD = 0.85
MIN_SUBSET_TOKENS = 3


def task_tokens(task: str) -> list[str]:
    t = task.lower().replace("'s ", " ").replace("’s ", " ")
    t = re.sub(r"[^a-z0-9%]+", " ", t)
    toks = [SPELLING.get(w, w) for w in t.split()]
    return [w for w in toks if w not in STOP_WORDS]


def task_key(task: str) -> str:
    return " ".join(task_tokens(task))


def similar(key_a: str, key_b: str) -> bool:
    a, b = set(key_a.split()), set(key_b.split())
    if not a or not b:
        return False
    if a == b:
        return True
    jaccard = len(a & b) / len(a | b)
    if jaccard >= THRESHOLD:
        return True
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return len(small) >= MIN_SUBSET_TOKENS and small <= big
