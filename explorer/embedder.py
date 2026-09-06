#!/usr/bin/env python3
"""
Pluggable text embedder — the ONE swappable component that turns Version-2's semantic distance
from a TF-IDF proxy into real neural embeddings (e.g. Qwen3-Embedding).

Two backends, same interface `encode(texts) -> np.ndarray (N, d)  L2-normalized`:
  local : sentence-transformers, open weights (default Qwen/Qwen3-Embedding-0.6B).
          runs on CPU/GPU, no per-call cost. Qwen3 supports an INSTRUCTION prompt that steers
          what the vector emphasizes (e.g. capability, ignoring language) — passed via `instruct`.
  api   : any OpenAI-compatible /embeddings endpoint that serves the model
          (DashScope / SiliconFlow / etc.). Set EMBED_BASE_URL + EMBED_API_KEY.

Everything downstream (kNN graph, clustering, the purity/silhouette validation) is identical to
the TF-IDF path — only the vectors change.
"""
from __future__ import annotations
import os, time


def get_embedder(backend="local", model="Qwen/Qwen3-Embedding-0.6B", instruct=None, batch=64, workers=8):
    import numpy as np

    if backend == "local":
        from sentence_transformers import SentenceTransformer
        st = SentenceTransformer(model)

        def encode(texts):
            # Qwen3-Embedding: an instruction can be given to documents/queries to steer the space.
            kw = {"normalize_embeddings": True, "batch_size": batch, "convert_to_numpy": True}
            if instruct:
                kw["prompt"] = f"Instruct: {instruct}\nText: "
            return st.encode(list(texts), **kw).astype("float32")
        return encode

    elif backend == "api":
        from openai import OpenAI
        base = os.environ.get("EMBED_BASE_URL", "https://openrouter.ai/api/v1")   # OpenRouter has /embeddings (2026)
        key = os.environ.get("EMBED_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise SystemExit("Set OPENROUTER_API_KEY (or EMBED_API_KEY)")
        client = OpenAI(base_url=base, api_key=key, default_headers={"X-Title": "SkillGrapher"})
        from concurrent.futures import ThreadPoolExecutor

        def encode(texts):
            texts = list(texts)
            batches = [texts[i:i + batch] for i in range(0, len(texts), batch)]
            res = [None] * len(batches)

            def do(bi):                                        # one batch, with retry
                chunk = batches[bi]
                if instruct:
                    chunk = [f"Instruct: {instruct}\nText: {t}" for t in chunk]
                for attempt in range(4):
                    try:
                        r = client.embeddings.create(model=model, input=chunk)
                        res[bi] = [d.embedding for d in r.data]; return
                    except Exception:
                        if attempt == 3: raise
                        time.sleep(1.5 * (attempt + 1))

            with ThreadPoolExecutor(max_workers=workers) as pool:  # batches run in PARALLEL
                list(pool.map(do, range(len(batches))))
            x = np.asarray([e for r in res for e in r], dtype="float32")  # concat in order
            x /= (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)        # L2-normalize
            return x
        return encode

    raise ValueError(f"unknown backend {backend!r}")
