from __future__ import annotations

import math
import re
from collections import Counter

ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:@-]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
STOPWORDS = {
    "如何",
    "怎么",
    "怎样",
    "什么",
    "为何",
    "为啥",
    "哪里",
    "在哪",
    "一下",
    "请问",
    "时先",
    "先查",
    "查什",
    "么操",
}


def _cjk_tokens(segment: str) -> list[str]:
    segment = segment.strip()
    if not segment:
        return []
    tokens: list[str] = []
    if len(segment) <= 8:
        tokens.append(segment)
    if len(segment) == 1:
        tokens.append(segment)
        return tokens
    for index in range(len(segment) - 1):
        tokens.append(segment[index : index + 2])
    return tokens


def tokenize(text: str, *, drop_stopwords: bool = False) -> list[str]:
    text = text.lower()
    tokens: list[str] = []
    for match in ASCII_TOKEN_RE.finditer(text):
        token = match.group(0).strip("._-/:@")
        if token:
            tokens.append(token)
    for match in CJK_RE.finditer(text):
        tokens.extend(_cjk_tokens(match.group(0)))
    if drop_stopwords:
        return [token for token in tokens if token not in STOPWORDS]
    return tokens


def token_counts(text: str, *, drop_stopwords: bool = False) -> Counter[str]:
    return Counter(tokenize(text, drop_stopwords=drop_stopwords))


def build_query_text(text: str) -> str:
    return " ".join(tokenize(text, drop_stopwords=True))


def compute_tfidf_weights(counts: Counter[str], idf_map: dict[str, float]) -> tuple[dict[str, float], float]:
    if not counts:
        return {}, 0.0
    total_terms = sum(counts.values()) or 1
    weights: dict[str, float] = {}
    norm = 0.0
    for token, count in counts.items():
        tf = count / total_terms
        idf = idf_map.get(token, 0.0)
        weight = tf * idf
        if weight <= 0:
            continue
        weights[token] = weight
        norm += weight * weight
    return weights, math.sqrt(norm)
