#!/usr/bin/env python3
"""Download the embedding files from Hugging Face.

Two files are too large for git — together well over a gigabyte — so they live on the Hub and
are pulled on demand:

    skill_embeddings.npy   one vector per skill; Add My Skill searches it for neighbours
    step_embeddings.npz    one vector per workflow step; highlights the operations two skills
                           share, the ×N badge in the relation list

Neither is needed to browse, search, or open a skill's graph. Fetch them when you want the
features they power.

    python tools/fetch_data.py             # both
    python tools/fetch_data.py --only skill_embeddings.npy

Resumable: a interrupted download continues from where it stopped rather than starting over.
Each file is verified against the corpus after arrival, because a truncated .npy still looks
like a file — it just fails much later, somewhere confusing.
"""
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The Hub account is not the GitHub one — no hyphen. Override with SF_HF_REPO if you
# mirror the files somewhere else.
REPO = os.environ.get("SF_HF_REPO", "JianhengLiu/skillfabri-data")
BASE = f"https://huggingface.co/datasets/{REPO}/resolve/main"

FILES = {
    "skill_embeddings.npy": "profile vectors — Add My Skill",
    "step_embeddings.npz":  "step vectors — shared-operation highlighting",
}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def download(name, dest, force=False):
    import requests
    url = f"{BASE}/{name}"
    tmp = dest.with_suffix(dest.suffix + ".part")

    head = requests.head(url, allow_redirects=True, timeout=30)
    # A dataset that does not exist answers 401, not 404 — the Hub will not reveal whether a
    # private repo is there. Either way the file is not reachable, and a traceback is no use.
    if head.status_code in (401, 403, 404):
        sys.exit(f"Cannot reach {name} in {REPO} (HTTP {head.status_code}).\n"
                 f"  · not uploaded yet?  see 'The embeddings' in the README\n"
                 f"  · somewhere else?    set SF_HF_REPO to that dataset repo\n"
                 f"  · private repo?      run: hf auth login   (huggingface_hub 1.x; "
                 f"the old huggingface-cli is gone)")
    head.raise_for_status()
    total = int(head.headers.get("content-length", 0))

    if dest.exists() and not force:
        if total and dest.stat().st_size == total:
            print(f"  {name}: already here ({human(total)})")
            return dest
        print(f"  {name}: local copy is {human(dest.stat().st_size)}, "
              f"remote is {human(total)} — refetching")

    have = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    if have:
        print(f"  {name}: resuming at {human(have)}")

    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        mode = "ab" if have and r.status_code == 206 else "wb"
        if mode == "wb":
            have = 0
        # \r only redraws on a terminal. Piped to a file or a CI log it would leave one line
        # per megabyte, so there it reports at intervals instead.
        tty = sys.stdout.isatty()
        with open(tmp, mode) as f:
            done, mark = have, have
            for chunk in r.iter_content(1 << 20):
                f.write(chunk); done += len(chunk)
                if not total:
                    continue
                if tty:
                    print(f"\r  {name}: {human(done)} / {human(total)}  "
                          f"{done / total * 100:5.1f}%", end="", flush=True)
                elif done - mark >= 32 << 20:
                    mark = done
                    print(f"  {name}: {human(done)} / {human(total)}", flush=True)
    if tty:
        print()
    tmp.replace(dest)
    return dest


def verify(name, path, n_skills):
    """A truncated array is still a file. Check it against the corpus it has to line up with."""
    import numpy as np
    if name.endswith(".npy"):
        X = np.load(path, mmap_mode="r")
        if X.shape[0] != n_skills:
            sys.exit(f"  {name}: has {X.shape[0]} rows but the corpus has {n_skills} skills. "
                     f"Row i must be skill i — this file belongs to a different corpus.")
        print(f"  {name}: ok, {X.shape[0]:,} x {X.shape[1]}")
    else:
        z = np.load(path, allow_pickle=True)
        print(f"  {name}: ok, {', '.join(z.files)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=list(FILES), help="fetch one file instead of both")
    ap.add_argument("--force", action="store_true", help="refetch even if the file looks complete")
    ap.add_argument("--dest", default=str(ROOT / "data"))
    a = ap.parse_args()

    dest = Path(a.dest); dest.mkdir(parents=True, exist_ok=True)
    n_skills = len(json.load(open(ROOT / "data" / "skills.json")))
    want = [a.only] if a.only else list(FILES)

    print(f"from https://huggingface.co/datasets/{REPO}")
    for name in want:
        print(f"\n{name}  —  {FILES[name]}")
        p = download(name, dest / name, a.force)
        verify(name, p, n_skills)

    print("\nDone. Restart the server to pick them up.")


if __name__ == "__main__":
    main()
