"""Deterministic sparse feature hash for dev/tests only. No claim of production semantic quality."""

import hashlib
import math
import re

from .types import EmbedRequest, EmbedResult, Usage


def fake_embedding(text: str) -> list[float]:
    aliases = {
        "dog": "pets",
        "dogs": "pets",
        "cat": "pets",
        "animals": "pets",
        "animal": "pets",
        "bicycle": "bike",
        "bicycles": "bike",
        "leave": "storage",
        "overnight": "storage",
        "automobile": "parking",
        "car": "parking",
        "vehicle": "parking",
        "wheelchair": "accessibility",
        "stairs": "accessibility",
        "step": "accessibility",
        "washroom": "toilet",
        "restroom": "toilet",
        "bathroom": "toilet",
        "money": "refund",
        "reimbursement": "refund",
        "return": "refund",
        "guarantee": "warranty",
        "guaranteed": "warranty",
        "promise": "warranty",
        "residue": "compost",
        "scraps": "compost",
        "organic": "compost",
    }
    stop = {"a", "the", "can", "i", "my", "is", "it", "do", "you", "your", "to", "for", "of", "with", "and"}
    vector = [0.0] * 1024
    for word in re.findall(r"\w+", text.casefold()):
        if word in stop:
            continue
        word = aliases.get(word, word)
        index = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4]) % 1024
        vector[index] += 1
    if not any(vector):
        vector[0] = 1
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector]


def embed(name: str, req: EmbedRequest) -> EmbedResult:
    return EmbedResult(
        embeddings=[fake_embedding(t) for t in req.inputs],
        provider=name,
        model=req.model,
        usage=Usage(input_tokens=max(1, sum(len(t.encode()) for t in req.inputs) // 4), output_tokens=0),
        latency_ms=1,
    )
