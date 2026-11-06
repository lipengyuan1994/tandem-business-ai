"""Exact body spans, 400-word windows with 60-word overlap, favoring heading boundaries."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str
    tokens: int


def chunks(body: str) -> list[Span]:
    words = list(re.finditer(r"\S+", body))
    out: list[Span] = []
    start = 0
    while start < len(words):
        end = min(start + 400, len(words))
        # Keep headings with their following paragraph where possible.
        if end < len(words):
            for index in range(start + 300, end):
                if words[index].group().startswith("#") and (
                    words[index].start() == 0 or body[words[index].start() - 1] == "\n"
                ):
                    end = index
                    break
        lo, hi = words[start].start(), words[end - 1].end()
        text = body[lo:hi]
        out.append(Span(lo, hi, text, max(1, len(text.encode()) // 4)))
        if end == len(words):
            break
        start = end - 60
    return out
