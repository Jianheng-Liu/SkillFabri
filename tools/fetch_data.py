#!/usr/bin/env python3
"""Download the large data files from Hugging Face.

Three files are too big for git, so they live on the Hub and are pulled on demand:

    skill_embeddings.npy   one vector per skill; Add My Skill searches it for neighbours
    step_embeddings.npz    one vector per workflow step; ranks which operations two skills
                           share when their wordings differ
    skillmd.zip            every skill's own SKILL.md; Merge reads two of them in full

None is needed to browse, search, or open a skill's graph. Fetch the ones whose features you
want. skillmd.zip is 40 MB, the two vector files together are well over a gigabyte.

    python tools/fetch_data.py             # all three
    python tools/fetch_data.py --only skillmd.zip

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
    "skill_embeddings.npy": "profile vectors, for Add My Skill",
    "step_embeddings.npz":  "step vectors, for shared-operation ranking",
    "skillmd.zip":          "every skill's SKILL.md, for Merge",
}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def sha256_of(path, chunk=1 << 22):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def download(name, dest, force=False):
    import requests
    url = f"{BASE}/{name}"
    tmp = dest.with_suffix(dest.suffix + ".part")

    head = requests.head(url, allow_redirects=True, timeout=30)
    # The Hub puts the file's sha256 in X-Linked-ETag, so the HEAD we already make is enough
    # to tell a current copy from a stale one. It rides on the 302 to the CDN, not on the
    # response that redirect lands on — the CDN sends an ETag of its own, which is a different
    # hash of the same bytes and matches nothing we can compute here.
    want_sha = None
    for resp in list(head.history) + [head]:
        v = (resp.headers.get("x-linked-etag") or "").strip('"')
        if len(v) == 64:
            want_sha = v
            break
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
            # Size alone said "already here" and was wrong: a local skill_embeddings.npy from
            # an earlier build was byte-for-byte the same length as the current one and a
            # different file, so it was never replaced and the neighbours it found came from
            # vectors the shipped graph was not built on.
            if want_sha and len(want_sha) == 64:
                have_sha = sha256_of(dest)
                if have_sha == want_sha:
                    print(f"  {name}: already here ({human(total)})")
                    return dest
                print(f"  {name}: same size as the remote but a different file "
                      f"({have_sha[:12]}… vs {want_sha[:12]}…) — refetching")
                tmp.unlink(missing_ok=True)    # a resume would append to the wrong bytes
            else:
                print(f"  {name}: already here ({human(total)})")
                return dest
        else:
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
    if want_sha and len(want_sha) == 64:
        got = sha256_of(tmp)
        if got != want_sha:
            sys.exit(f"  {name}: downloaded {human(done)} but the checksum does not match "
                     f"({got[:12]}… vs {want_sha[:12]}…). Delete the .part file and retry.")
    tmp.replace(dest)
    return dest


def verify(name, path, n_skills):
    """A truncated array is still a file. Check it against the corpus it has to line up with."""
    import numpy as np
    if name.endswith(".zip"):
        # a truncated zip still opens; it is the central directory at the end that decides,
        # and testzip reads every member rather than trusting the listing
        import zipfile
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                sys.exit(f"  {name}: {bad} is corrupt. Delete it and fetch again.")
            print(f"  {name}: ok, {len(z.namelist()):,} documents")
        return
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
    ap.add_argument("--only", choices=list(FILES), help="fetch one file instead of all three")
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
