#!/usr/bin/env python3
"""Build data/skill_embeddings.npy — the one file Add My Skill needs and git cannot hold.

Placing a skill means finding its neighbours, which is a cosine search over one vector per
skill in the corpus. That array is about 170 MB, so it is not committed; this rebuilds it from
data/skills.json, which is.

    export OPENROUTER_API_KEY=...
    python tools/build_embeddings.py

Roughly 11k short texts through an embedding model. Embeddings are far cheaper than chat —
expect cents, not dollars — and the result is cached, so this is a one-off.

The profile template below has to match the one the graph was built with, and the one app.py
uses when it embeds *your* skill. If they drift, your skill is measured against a different
space than the corpus and the neighbours it finds are meaningless.
"""
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "explorer"))

INSTRUCT = ("Represent the software skill's function and capability for retrieval, "
            "ignoring programming language")
MODEL = "qwen/qwen3-embedding-8b"


def profile_text(s):
    """Verbatim from the pipeline that built the shipped graph. Do not 'improve' it."""
    prim = s.get("primary") or (s.get("task") or [["?"]])[0][0]
    cl = s.get("cluster") or ""
    sub = s.get("bu_sub") or s.get("sub") or ""
    obj = s.get("object") or ""
    tg = s.get("tags", {}) or {}
    facets = ", ".join(tg.get("tech", []) + tg.get("domain", []) + tg.get("concern", [])) or "—"
    io = ("consumes " + (", ".join(s.get("input", []) or []) or "—") +
          "; produces " + (", ".join((s.get("produces") or []) or s.get("output", []) or []) or "—"))
    return (f"Activity: {prim}. Cluster: {cl}. Function: {sub}. Object: {obj}. "
            f"Subject: {s.get('subject','')}. "
            f"Operations: {'; '.join(s.get('steps') or [])}. "
            f"Summary: {(s.get('summary') or s.get('desc') or '')}. "
            f"Facets: {facets}. {io}.")[:2000]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=str(ROOT / "data" / "skills.json"))
    ap.add_argument("--output", default=str(ROOT / "data" / "skill_embeddings.npy"))
    ap.add_argument("--backend", default="api", choices=["api", "local"],
                    help="api: OpenRouter (needs OPENROUTER_API_KEY). "
                         "local: sentence-transformers, no key, needs the model downloaded")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()

    out = Path(a.output)
    if out.exists() and not a.overwrite:
        print(f"{out} already exists — pass --overwrite to rebuild")
        return

    if a.backend == "api" and not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("OPENROUTER_API_KEY is not set. Put it in env.sh and `source env.sh`, "
                 "or use --backend local.")

    import numpy as np
    from embedder import get_embedder

    skills = json.load(open(a.input))
    texts = [profile_text(s) for s in skills]
    print(f"embedding {len(texts):,} skills with {MODEL} via {a.backend} …")

    enc = get_embedder(a.backend, MODEL, INSTRUCT, workers=a.workers)
    X = enc(texts).astype("float32")

    if X.shape[0] != len(skills):
        sys.exit(f"got {X.shape[0]} vectors for {len(skills)} skills — refusing to write a "
                 "misaligned array, since row i must be skill i")

    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, X)
    print(f"wrote {out}  {X.shape}  {out.stat().st_size / 1e6:.0f} MB")
    print("Add My Skill is now available — restart the server.")


if __name__ == "__main__":
    main()
