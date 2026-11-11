"""
Bengali question -> English document retrieval, top-1 and top-3, per embedding model via OpenRouter.

    OPENROUTER_API_KEY=... python docs/evals/bengali_retrieval.py [model ...]

Cases live in bengali_retrieval.json (`gold` = index of the right doc). Embeddings are compared by cosine similarity.
The set is small (8 cases): a score is a rough signal, not a benchmark. Add cases before trusting small gaps.
"""
import json
import math
import os
import sys
from pathlib import Path

import requests

URL = "https://openrouter.ai/api/v1/embeddings"
DEFAULT_MODELS = [
    "cohere/embed-5-fast",
    "intfloat/multilingual-e5-large",
    "baai/bge-m3",
    "qwen/qwen3-embedding-8b",
]
# e5 was trained with these prefixes (the old TEIEmbeddings added them); other models get none.
E5_PREFIXES = ("query: ", "passage: ")


def embed(model: str, texts: list[str], kind: str, key: str) -> list[list[float]]:
    body = {"model": model, "input": texts}
    if model.startswith("cohere/"):
        body["input_type"] = "search_query" if kind == "query" else "search_document"
    if model.startswith("intfloat/e5"):
        body["input"] = [(E5_PREFIXES[0] if kind == "query" else E5_PREFIXES[1]) + t for t in texts]
    resp = requests.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    if resp.status_code != 200:
        raise SystemExit(f"{model}: HTTP {resp.status_code}: {resp.text[:300]}")
    data = sorted(resp.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def score(model: str, spec: dict, key: str) -> tuple[int, int, int]:
    docs = embed(model, spec["docs"], "doc", key)
    queries = embed(model, [c["question"] for c in spec["cases"]], "query", key)
    top1 = top3 = 0
    for case, q in zip(spec["cases"], queries):
        ranked = sorted(range(len(docs)), key=lambda i: cosine(q, docs[i]), reverse=True)
        top1 += ranked[0] == case["gold"]
        top3 += case["gold"] in ranked[:3]
    return top1, top3, len(spec["cases"])


def main() -> None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    spec = json.loads((Path(__file__).parent / "bengali_retrieval.json").read_text(encoding="utf-8"))
    for model in sys.argv[1:] or DEFAULT_MODELS:
        top1, top3, n = score(model, spec, key)
        print(f"{model:40s} top-1 {top1}/{n}   top-3 {top3}/{n}")


if __name__ == "__main__":
    main()
