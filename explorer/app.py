#!/usr/bin/env python3
"""
SkillFabri Explorer — a user-facing web app over the frozen skill graph. Full-width portal to:
  · see the most-popular skills and the market's redundancy at a glance
  · search skills; browse by activity / capability / object / concern (ranked by popularity)
  · open a skill and see its same / intersect / contain relations
  · PLACE YOUR OWN skill: paste a name+summary+steps, we embed it, find its neighbours in the graph,
    type the relations, and show where it would connect.

Serves the SPA (explorer.html) + a small JSON API. Run locally:
  source env.sh
  python explorer/app.py --port 8000
"""
from __future__ import annotations
import argparse, json, os, re, threading, collections
from pathlib import Path
import numpy as np
from flask import Flask, request, jsonify, send_file, redirect
from urllib.parse import quote
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # `python explorer/app.py` too

HERE = Path(__file__).resolve().parent      # explorer/
ROOT = HERE.parent                          # repo root
DATA = ROOT / "data"
def norm(x): return re.sub(r"[^a-z0-9]+", "-", str(x).strip().lower()).strip("-")

app = Flask(__name__)
D = {}   # loaded state


# ---------- response cache ----------
# The corpus is immutable once loaded, so every read endpoint is a pure function of its query
# string: the same URL returns the same bytes for the life of the process. Measured, one graph
# request costs ~0.17s of Python, and with one core and the GIL that set the ceiling at about
# 5 requests a second no matter how many arrived. Caching the finished body removes both the
# computation and the re-serialisation.
#
# Applied only to routes that read the corpus. Anything shaped by who is asking — /api/me,
# /api/mine — must never be cached, or one visitor would be served another's list.
_CACHE = collections.OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 600
_CACHE_HITS = [0, 0]        # hits, misses


def cached(fn):
    """Memoise a GET route on its full query string, LRU-bounded."""
    from functools import wraps

    @wraps(fn)
    def wrap(*a, **kw):
        key = request.path + "?" + request.query_string.decode("utf-8", "replace")
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit is not None:
                _CACHE.move_to_end(key)
                _CACHE_HITS[0] += 1
        if hit is not None:
            return app.response_class(hit, mimetype="application/json")
        _CACHE_HITS[1] += 1
        resp = fn(*a, **kw)
        # only store plain successful JSON; an error should not be pinned for the process's life
        if getattr(resp, "status_code", 200) == 200 and resp.mimetype == "application/json":
            body = resp.get_data()
            with _CACHE_LOCK:
                _CACHE[key] = body
                while len(_CACHE) > _CACHE_MAX:
                    _CACHE.popitem(last=False)
        return resp
    return wrap


@app.get("/api/cache")
def cache_stats():
    h, m = _CACHE_HITS
    return jsonify({"entries": len(_CACHE), "hits": h, "misses": m,
                    "rate": round(h / (h + m), 3) if h + m else 0})

def load(graph_file="relations.json"):
    nodes = json.load(open(DATA / "skills.json"))
    G = json.load(open(DATA / graph_file))
    nbr = G["nbr"]
    # profile embeddings are optional: they power the "similar" (near) layer and "Add My Skill".
    # Absent -> the graph still works from the verified relations; those two features degrade.
    X = None
    _emb = DATA / "skill_embeddings.npy"
    if _emb.exists():
        X = np.load(_emb).astype("float32")
        X /= (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    else:
        print("[explorer] skill_embeddings.npy absent — 'similar' layer + add-your-skill disabled (see README to enable)")
    N = len(nodes)
    # compact per-skill record for the client
    def rec(i):
        s = nodes[i]; tg = s.get("tags", {}) or {}
        return {"id": i, "name": s.get("name") or "?", "pop": int(s.get("popularity") or 0),
                "activity": s.get("primary") or "—", "capability": s.get("capability") or "—",
                "object": s.get("object") or "—", "fine": s.get("fine") or "—",
                "summary": (s.get("summary") or s.get("desc") or "")[:400],
                "tech": (tg.get("tech") or [])[:10], "domain": tg.get("domain") or [], "concern": tg.get("concern") or [],
                "steps": (s.get("steps") or [])[:12], "url": s.get("url") or "", "market": s.get("market") or "",
                "author": s.get("author") or "", "repo": s.get("repo") or "", "repo_url": s.get("repo_url") or "",
                "updated": s.get("updated") or "", "occupation": s.get("occupation") or "",
                "quality": s.get("quality") or {}, "pop_src": s.get("pop_src") or "",
                "tools": (s.get("tools") or [])[:10]}
    recs = [rec(i) for i in range(N)]
    # typed relation adjacency (undirected same/intersect; directed contain)
    rel = [[] for _ in range(N)]
    for i, row in enumerate(nbr):
        for e in row:
            j, d, t = e[0], e[1], e[2]
            typ = "same" if t == 1 else ("contain" if t == 2 else "intersect")
            dir_ = e[3] if t == 2 else 0
            rel[i].append({"id": j, "type": typ, "sim": round(1 - d, 3), "dir": dir_})
    for i in range(N): rel[i].sort(key=lambda r: ({"same": 0, "contain": 1, "intersect": 2}[r["type"]], -r["sim"]))
    # dimension groupings -> value -> [skill idxs] (popularity-sorted)
    groups = {"activity": collections.defaultdict(list), "capability": collections.defaultdict(list),
              "object": collections.defaultdict(list), "concern": collections.defaultdict(list),
              "domain": collections.defaultdict(list), "tool": collections.defaultdict(list),
              "occupation": collections.defaultdict(list)}
    for i, r in enumerate(recs):
        groups["activity"][r["activity"]].append(i); groups["capability"][r["capability"]].append(i)
        groups["object"][r["object"]].append(i)
        for c in r["concern"]: groups["concern"][c].append(i)
        for dv in r["domain"]: groups["domain"][dv].append(i)
    pop_order = sorted(range(N), key=lambda i: -recs[i]["pop"])
    for dim in groups:
        for v in groups[dim]: groups[dim][v].sort(key=lambda i: -recs[i]["pop"])
    # ---- same-clusters (union-find) + contain closure (for propagating a new skill's relations) ----
    par = list(range(N))
    def find(x):
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    for i in range(N):
        for e in rel[i]:
            if e["type"] == "same": par[find(i)] = find(e["id"])
    clus = collections.defaultdict(list)
    for i in range(N): clus[find(i)].append(i)
    same_cluster = {i: clus[find(i)] for i in range(N)}
    narrower = collections.defaultdict(set); broader = collections.defaultdict(set)   # i⊇narrower[i]; broader[i]⊇i
    for i in range(N):
        for e in rel[i]:
            if e["type"] == "contain" and e["dir"] == 1: narrower[i].add(e["id"]); broader[e["id"]].add(i)
    def closure(adjmap):
        out = {}
        for x in list(adjmap):
            seen = set(); st = list(adjmap[x])
            while st:
                y = st.pop()
                if y in seen: continue
                seen.add(y); st += list(adjmap.get(y, ()))
            out[x] = seen
        return out
    desc, anc = closure(narrower), closure(broader)
    # Counts shown as badges on every card, so the graph is visible without opening a skill.
    # `same` counts this skill's own edges, not its cluster. The cluster is a transitive closure,
    # and near-duplicate is not transitive: in a star, the three spokes were each judged against
    # the hub and never against each other, so reporting a cluster of four as "3 duplicates" on a
    # spoke asserts two comparisons nobody made — and the card then disagreed with the graph and
    # the relation list it opens, which both show edges. Cluster size is still a real quantity;
    # it belongs to /api/cluster and the redundancy view, which say so.
    badge = []
    for i in range(N):
        c = collections.Counter(e["type"] for e in rel[i])
        badge.append({"same": c["same"], "intersect": c["intersect"], "contain": c["contain"]})
    # redundancy per repo: how much of a publisher's own catalogue duplicates itself
    byrepo = collections.defaultdict(list)
    for i, nd in enumerate(nodes):
        if nd.get("repo"): byrepo[nd["repo"]].append(i)
    repo_stats = []
    for r, idx in byrepo.items():
        dup = len(idx) - len({find(i) for i in idx})
        repo_stats.append({"repo": r, "n": len(idx), "dup": dup,
                           "author": nodes[idx[0]].get("author") or "", "ids": idx[:200]})
    repo_stats.sort(key=lambda x: (-x["dup"], -x["n"]))
    # ---- step embeddings (for highlighting the intersecting step) ----
    Xst = stepidx = None
    try:
        zz = np.load(DATA / "step_embeddings.npz", allow_pickle=True)
        Xst = zz["X"].astype("float32"); Xst /= (np.linalg.norm(Xst, axis=1, keepdims=True) + 1e-9)
        vix = {norm(v): k for k, v in enumerate(zz["vocab"])}
        stepidx = [[vix[norm(x)] for x in (nd.get("steps") or [])[:10] if norm(x) in vix] for nd in nodes]
        print(f"[explorer] step embeddings loaded ({Xst.shape[0]} steps)")
    except Exception as ex:
        print(f"[explorer] step embeddings unavailable ({ex}); step-highlight off")
    # extra browse dimensions the other marketplaces have and we can support
    for i, nd in enumerate(nodes):
        for t in (nd.get("tools") or [])[:10]: groups["tool"][t].append(i)
        if nd.get("occupation"): groups["occupation"][nd["occupation"]].append(i)
    for dim in ("tool", "occupation"):
        for v in groups[dim]: groups[dim][v].sort(key=lambda i: -recs[i]["pop"])
    D.update(nodes=nodes, recs=recs, rel=rel, X=X, N=N, groups=groups, pop_order=pop_order,
             same_cluster=same_cluster, desc=desc, anc=anc, Xst=Xst, stepidx=stepidx,
             badge=badge, repo_stats=repo_stats, clusters=clus, find=find)
    lr, mm = G.get("intersect_rule"), G.get("intersect_min_match")
    if lr:    rule = f"intersect = LLM >={lr['min_ops']} shared operations ({lr['orders']} orders, {lr['combine']})"
    elif mm:  rule = f"intersect = >={mm['k']} matched steps (tau {mm['tau']}, {mm['mode']})"
    else:     rule = "intersect = >=1 concrete step (legacy)"
    print(f"[explorer] loaded {N} skills from {graph_file}, {sum(len(v) for v in rel)//2} relation-endpoints | {rule}")

# a step of i "matches" j when its best cosine against any step of j clears this bar.
# Same criterion (and value) the pipeline uses — flow_distance.py TAU and
# tighten_intersect_minmatch.py --tau — so the highlight shows exactly the steps
# that the >=K-matched-steps intersect rule counted.
MATCH_TAU = 0.70

def match_steps(i, j):
    """ALL indices of skill i's steps that match some step of skill j (cos >= MATCH_TAU),
    strongest first. Empty when there is no step-level overlap (or no step embeddings)."""
    if D["Xst"] is None: return []
    ai, bj = D["stepidx"][i], D["stepidx"][j]
    if not ai or not bj: return []
    best = (D["Xst"][ai] @ D["Xst"][bj].T).max(axis=1)
    hits = [k for k in range(len(ai)) if best[k] >= MATCH_TAU]
    return sorted(hits, key=lambda k: -float(best[k]))

# ---------- helpers ----------
def search_ids(q, limit=400):
    q = q.lower().strip()
    if not q: return []
    out = []
    for i, r in enumerate(D["recs"]):
        hay = (r["name"] + " " + r["summary"] + " " + r["fine"] + " " + " ".join(r["tech"])).lower()
        if q in hay:
            score = (3 if q in r["name"].lower() else 0) + (2 if q in r["fine"].lower() else 0) + r["pop"] / 1e6
            out.append((score, i))
    out.sort(key=lambda x: -x[0]); return [i for _, i in out[:limit]]

# ---------- API ----------
def can_place():
    """Whether this instance can run the add-a-skill pipeline.

    It needs the profile embeddings to find neighbours, and an OpenRouter key to label the
    pasted file. The public deployment has neither, on purpose: the embeddings are a gigabyte
    and the key would let any visitor spend the owner's money. Run it locally to get both."""
    return D.get("X") is not None and bool(os.environ.get("OPENROUTER_API_KEY"))


@app.get("/api/caps")
@cached
def caps():
    return jsonify({"add": can_place(),
                    "why": {"embeddings": D.get("X") is not None,
                            "api_key": bool(os.environ.get("OPENROUTER_API_KEY"))}})


@app.get("/api/overview")
@cached
def overview():
    recs = D["recs"]; N = D["N"]
    nCap = len({r["capability"] for r in recs})
    withRel = sum(1 for r in D["rel"] if r)
    same_pairs = sum(1 for i in range(N) for e in D["rel"][i] if e["type"] == "same" and i < e["id"])
    inter = sum(1 for i in range(N) for e in D["rel"][i] if e["type"] == "intersect" and i < e["id"])
    contain = sum(1 for i in range(N) for e in D["rel"][i] if e["type"] == "contain" and e["dir"] == 1)
    acts = sorted(((v, len(ids)) for v, ids in D["groups"]["activity"].items()), key=lambda x: -x[1])
    # dedupe clusters — the headline. 25% of the market is a near-duplicate of something else.
    clus = [v for v in D["clusters"].values() if len(v) > 1]
    clus.sort(key=len, reverse=True)
    in_cluster = sum(len(v) for v in clus)
    big = [{"n": len(v), "name": recs[v[0]]["name"], "activity": recs[v[0]]["activity"],
            "ids": v[:12]} for v in clus[:8]]
    # publishers whose own catalogue duplicates itself
    repos = [r for r in D["repo_stats"] if r["n"] >= 20][:8]
    return jsonify({"n_skills": N, "n_cap": nCap, "avg_per_cap": round(N / nCap, 1),
                    "overlap_pct": round(100 * withRel / N),
                    "same": same_pairs, "intersect": inter, "contain": contain,
                    "n_clusters": len(clus), "in_cluster": in_cluster,
                    "dup_pct": round(100 * in_cluster / N), "biggest": big,
                    "repos": [{"repo": r["repo"], "author": r["author"], "n": r["n"], "dup": r["dup"],
                               "pct": round(100 * r["dup"] / r["n"])} for r in repos],
                    "activities": [{"name": a, "count": c} for a, c in acts]})


@app.get("/api/demo-graph")
@cached
def demo_graph():
    """A real neighbourhood for the landing page's graph section — one skill and its relations,
    chosen for legibility: all three relation types present and no two skills sharing a name.
    Every edge below is an edge in the shipped graph; nothing here is illustrative."""
    recs, rel = D["recs"], D["rel"]
    def key(x): return "".join(c for c in x.lower() if c.isalnum())
    best = None
    for i in range(D["N"]):
        kinds = {e["type"] for e in rel[i]}
        if len(kinds) < 3: continue
        seen, names = [i], {key(recs[i]["name"])}
        for want in ("contain", "same", "intersect"):
            for e in sorted(rel[i], key=lambda e: -recs[e["id"]]["pop"]):
                if e["type"] != want or e["id"] in seen: continue
                if key(recs[e["id"]]["name"]) in names: continue
                seen.append(e["id"]); names.add(key(recs[e["id"]]["name"]))
                if len(seen) >= 11: break
            if len(seen) >= 11: break
        if len(seen) < 9: continue
        caps = len({recs[k]["capability"] for k in seen})
        if best is None or (caps, len(seen)) > best[0]: best = ((caps, len(seen)), seen)
    if not best: return jsonify({"nodes": [], "edges": []})
    ids = best[1]; iset = set(ids)
    edges, seen_pair = [], set()
    for a in ids:
        for e in rel[a]:
            b_ = e["id"]
            if b_ not in iset: continue
            k = (a, b_) if a < b_ else (b_, a)
            if k in seen_pair: continue
            seen_pair.add(k)
            s_, t_ = (a, b_) if e["type"] != "contain" or e["dir"] == 1 else (b_, a)
            edges.append({"s": s_, "t": t_, "type": e["type"]})
    order = {"contain": 0, "same": 1, "intersect": 2}
    edges.sort(key=lambda e: order[e["type"]])
    edges = [e for e in edges if e["type"] != "intersect"][:14] + \
            [e for e in edges if e["type"] == "intersect"][:12]
    deg = collections.Counter()
    for e in edges: deg[e["s"]] += 1; deg[e["t"]] += 1
    caps = sorted({recs[k]["capability"] for k in ids})
    return jsonify({
        "nodes": [{"id": k, "name": recs[k]["name"], "capability": recs[k]["capability"],
                   "cap_i": caps.index(recs[k]["capability"]), "deg": deg[k],
                   "activity": recs[k]["activity"]} for k in ids],
        "edges": edges, "activity": recs[ids[0]]["activity"], "capabilities": caps})


@app.get("/api/cluster")
@cached
def cluster():
    """Every member of one skill's dedupe cluster — the drill-down behind the redundancy headline."""
    i = int(request.args.get("id", -1))
    if i < 0 or i >= D["N"]: return jsonify({"error": "not found"}), 404
    ids = sorted(D["same_cluster"].get(i, [i]), key=lambda k: -D["recs"][k]["pop"])
    return jsonify({"n": len(ids), "items": [slim(D["recs"][k]) for k in ids]})

def slim(r):
    i = r["id"]
    return {"id": i, "name": r["name"], "pop": r["pop"], "activity": r["activity"],
            "capability": r["capability"], "summary": r["summary"][:180], "tech": r["tech"][:5], "object": r["object"],
            "author": r["author"], "repo": r["repo"], "repo_url": r["repo_url"],
            "updated": r["updated"], "pop_src": r["pop_src"], "quality": r["quality"],
            "rel": D["badge"][i]}

# A single "popularity" order mixes two incompatible quantities (a repo's stars vs a skill's own
# installs), so the sort is explicit and each option says what it measures.
SORTS = {
    "pop":      ("popularity",       lambda i: -D["recs"][i]["pop"]),
    "dups":     ("most duplicated",  lambda i: -D["badge"][i]["same"]),
    "shared":   ("most connected",   lambda i: -D["badge"][i]["intersect"]),
    "updated":  ("recently updated", lambda i: (D["recs"][i]["updated"] == "", 
                                                "" if not D["recs"][i]["updated"] else
                                                [-ord(c) for c in D["recs"][i]["updated"]])),
    "installs": ("installs",         lambda i: -(D["recs"][i]["pop"] if D["recs"][i]["pop_src"] == "clawhub_downloads" else -1)),
    "name":     ("name",             lambda i: D["recs"][i]["name"].lower()),
}


@app.get("/api/list")
@cached
def list_():
    dim = request.args.get("dim", ""); val = request.args.get("val", ""); act = request.args.get("act", "")
    q = request.args.get("q", ""); page = int(request.args.get("page", 0)); size = 40
    sort = request.args.get("sort", "pop"); has = request.args.get("has", "")
    if q: ids = search_ids(q)
    elif dim and dim in D["groups"]: ids = list(D["groups"][dim].get(val, []))
    else: ids = list(D["pop_order"])
    if act:                                          # scope to an activity (e.g. a capability node under one activity)
        aset = set(D["groups"]["activity"].get(act, [])); ids = [i for i in ids if i in aset]
    if has == "quality":  ids = [i for i in ids if D["recs"][i]["quality"]]
    elif has == "installs": ids = [i for i in ids if D["recs"][i]["pop_src"] == "clawhub_downloads"]
    elif has == "dups":   ids = [i for i in ids if D["badge"][i]["same"]]
    if sort in SORTS and sort != "pop": ids.sort(key=SORTS[sort][1])
    elif sort == "pop" and (q or dim or act or has): ids.sort(key=SORTS["pop"][1])
    total = len(ids); pg = ids[page * size:(page + 1) * size]
    return jsonify({"total": total, "page": page, "size": size, "sort": sort,
                    "sorts": [{"k": k, "label": v[0]} for k, v in SORTS.items()],
                    "items": [slim(D["recs"][i]) for i in pg]})

@app.get("/api/tree")
@cached
def tree():
    acts = sorted(D["groups"]["activity"].items(), key=lambda kv: -len(kv[1]))
    children = []
    for a, idxs in acts:
        caps = collections.Counter(D["recs"][i]["capability"] for i in idxs)
        capch = [{"name": c, "value": n, "act": a, "cap": c} for c, n in caps.most_common()]
        children.append({"name": a, "value": len(idxs), "act": a, "children": capch})
    return jsonify({"name": "All skills", "value": D["N"], "children": children})

@app.get("/api/dims")
@cached
def dims():
    out = {}
    for dim in ("activity", "capability", "object", "concern", "domain", "tool", "occupation"):
        vals = sorted(((v, len(ids)) for v, ids in D["groups"][dim].items()), key=lambda x: -x[1])
        out[dim] = [{"val": v, "count": c} for v, c in vals]
    return jsonify(out)

@app.get("/api/skill")
@cached
def skill():
    i = int(request.args.get("id", -1))
    if i < 0 or i >= D["N"]: return jsonify({"error": "not found"}), 404
    r = dict(D["recs"][i])
    r["relations"] = [{**e, "name": D["recs"][e["id"]]["name"], "activity": D["recs"][e["id"]]["activity"],
                       "capability": D["recs"][e["id"]]["capability"], "sis": match_steps(i, e["id"])} for e in D["rel"][i]]   # ALL relations (+ every intersecting step)
    r["counts"] = collections.Counter(e["type"] for e in D["rel"][i])
    return jsonify(r)

# ---------- place a NEW skill ----------
_ENC = [None]; _LOCK = threading.Lock()
def encoder():
    with _LOCK:
        if _ENC[0] is None:
            from embedder import get_embedder
            _ENC[0] = get_embedder("api", "qwen/qwen3-embedding-8b",
                                   "Represent the software skill's function and capability for retrieval, ignoring programming language", workers=4)
    return _ENC[0]

# Stage A: high-recall gate (mirrors the graph pipeline's Stage A — liberal on contain)
TYPE_SYS = ("You classify how a NEW software-engineering agent skill relates to each CANDIDATE skill. Decide by what "
            "each DOES — subject, workflow, tech — not wording. For EACH candidate: \"same\" (near-duplicate, same job "
            "& stack), \"contain\" (one is a strict superset — set broader to 'new' or 'cand'), \"intersect\" (share a "
            "real workflow step/purpose), or \"unrelated\". When unsure between contain and same/intersect, "
            "prefer contain (a strict check follows). Return ONLY JSON {\"1\":{\"type\":\"..\",\"broader\":\"new|cand|\"}, ...}.")
# Stage B: strict verification (mirrors the refilter — defaults to the WEAKER relation)
STRICT_SYS = ("Classify the relation between two SE agent skills A and B by what each DOES; DEFAULT to the WEAKER "
              "relation when unsure. \"same\"=near-duplicate (same job, subject AND stack). \"contain\"=one is a STRICT "
              "superset (does everything the other does PLUS concrete more; set broader A|B). \"intersect\"=share a real "
              "workflow step/purpose but neither duplicates nor strictly contains. \"unrelated\"=no shared work. A "
              "different language/framework, or a broader-vs-narrower scope, is \"intersect\", NOT \"same\". Return ONLY "
              "JSON {\"1\":{\"type\":\"..\",\"broader\":\"A|B|\"}, ...} — one verdict per pair.")

@app.post("/api/place")
def place():
    if not can_place():
        return jsonify({"error": "This instance cannot place skills. Run SkillFabri locally with "
                                 "the embeddings and your own OPENROUTER_API_KEY."}), 503
    if D.get("X") is None:
        return jsonify({"error": "Add-your-skill needs skill_embeddings.npy (see README to enable)."}), 503
    b = request.get_json(force=True) or {}
    labels = None; md = (b.get("md") or "").strip()
    if md:
        labels = label_md(md)
        name = (b.get("name") or labels["name"] or "").strip(); summary = labels["summary"]
        steps = labels["steps"]; tech = labels["tech"]; act_filter = b.get("activity") or ""
    else:
        name = (b.get("name") or "").strip(); summary = (b.get("summary") or "").strip()
        steps = [s for s in (b.get("steps") or []) if s.strip()]; tech = [t for t in (b.get("tech") or []) if t.strip()]
        act_filter = b.get("activity") or ""
    if not (summary or steps): return jsonify({"error": "give at least a summary or steps (or an md file)"}), 400
    ptext = (f"Subject: {name}. Capability: . Operations: {'; '.join(steps)}. Summary: {summary}. "
             f"Tech: {', '.join(tech) or '—'}. consumes —; produces —.")[:2000]
    v = encoder()([ptext])[0].astype("float32"); v /= (np.linalg.norm(v) + 1e-9)
    sims = D["X"] @ v; order = np.argsort(-sims)
    FLOOR = 0.70
    cand = [int(i) for i in order if sims[int(i)] >= FLOOR and (not act_filter or D["recs"][int(i)]["activity"] == act_filter)][:40]
    if not cand: cand = [int(i) for i in order[:8]]                              # fallback if nothing clears the floor
    top10 = cand[:10]
    sugg_act = collections.Counter(D["recs"][i]["activity"] for i in top10).most_common(1)[0][0] if top10 else "—"
    _sc = collections.Counter(D["recs"][i]["capability"] for i in top10 if D["recs"][i]["activity"] == sugg_act).most_common(1)
    sugg_cap = _sc[0][0] if _sc else "—"
    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"], default_headers={"X-Title": "SkillFabri"})
    def card(i):
        r = D["recs"][i]; return f"{r['name']}: {r['summary'][:180]} | steps: {'; '.join(r['steps'][:6])} | tech: {', '.join(r['tech'][:5])}"
    ref = f"{name}: {summary} | steps: {'; '.join(steps)} | tech: {', '.join(tech)}"
    def parse(txt): m = re.search(r"\{.*\}", txt or "", re.DOTALL); return (json.loads(m.group(0)) if m else {})
    typed = {}                                                                  # id -> {type, broader('new'|'cand'|'')}
    try:
        body = "\n".join(f"[{k+1}] {card(i)}" for k, i in enumerate(cand))
        rr = client.chat.completions.create(model="google/gemini-3.7-flash", temperature=0,
             messages=[{"role": "system", "content": TYPE_SYS}, {"role": "user", "content": f"NEW SKILL: {ref}\n\nCANDIDATES:\n{body}\n\nClassify each."}])
        objA = parse(rr.choices[0].message.content)
        for k, i in enumerate(cand):
            o = objA.get(str(k + 1)) or {}; t = str(o.get("type", "")).lower()
            if t in ("same", "contain", "intersect"): typed[i] = {"type": t, "broader": str(o.get("broader", "")).lower()}
        # Stage B: strictly re-verify only the same/contain candidates (default to weaker)
        sc = [i for i in cand if typed.get(i, {}).get("type") in ("same", "contain")]
        if sc:
            body2 = "\n".join(f"[{k+1}] A = {ref}\n     B = {card(i)}" for k, i in enumerate(sc))
            rr2 = client.chat.completions.create(model="google/gemini-3.7-flash", temperature=0,
                  messages=[{"role": "system", "content": STRICT_SYS}, {"role": "user", "content": f"Pairs:\n{body2}\n\nClassify each pair."}])
            objB = parse(rr2.choices[0].message.content)
            for k, i in enumerate(sc):
                o = objB.get(str(k + 1)) or {}; t = str(o.get("type", "")).lower(); br = str(o.get("broader", "")).upper()
                if t == "same": typed[i] = {"type": "same", "broader": ""}
                elif t == "contain": typed[i] = {"type": "contain", "broader": "new" if br == "A" else ("cand" if br == "B" else "")}
                else: typed[i] = {"type": "intersect", "broader": ""}            # demoted by the strict check
    except Exception:
        pass
    # ---- propagate: same -> same-cluster; contain -> transitive closure ----
    final = {i: {**ty, "prop": False} for i, ty in typed.items()}
    for i, ty in list(typed.items()):
        if ty["type"] == "same":
            for m in D["same_cluster"].get(i, []):
                if m != i and m not in final: final[m] = {"type": "same", "broader": "", "prop": True}
        elif ty["type"] == "contain" and ty["broader"] == "new":
            for dj in D["desc"].get(i, ()):                                       # new ⊇ i ⊇ dj
                if dj not in final: final[dj] = {"type": "contain", "broader": "new", "prop": True}
        elif ty["type"] == "contain" and ty["broader"] == "cand":
            for aj in D["anc"].get(i, ()):                                        # aj ⊇ i ⊇ new
                if aj not in final: final[aj] = {"type": "contain", "broader": "cand", "prop": True}
    ids = sorted(final, key=lambda i: -float(sims[i]))[:45]
    neigh = []
    for i in ids:
        r = D["recs"][i]; ty = final[i]
        neigh.append({"id": i, "name": r["name"], "activity": r["activity"], "capability": r["capability"],
                      "summary": r["summary"][:140], "pop": r["pop"], "sim": round(float(sims[i]), 3),
                      "type": ty["type"], "broader": ty.get("broader", ""), "prop": ty["prop"]})
    # ---- edges AMONG the neighbours (existing relations between them) ----
    S = set(ids); nedges = []; seen = set()
    for a in S:
        for e in D["rel"][a]:
            b2 = e["id"]
            if b2 in S:
                k = (a, b2) if a < b2 else (b2, a)
                if k in seen: continue
                seen.add(k)
                nedges.append({"a": a, "b": b2, "type": e["type"],
                               "dir": e["dir"] if e["type"] == "contain" else 0})
    nedges = sorted(nedges, key=lambda e: {"same": 0, "contain": 1, "intersect": 2}[e["type"]])[:400]
    n_typed = sum(1 for i in typed if typed[i]["type"] in ("same", "contain", "intersect"))
    return jsonify({"labels": labels, "suggested_activity": sugg_act, "suggested_capability": sugg_cap,
                    "n_typed": n_typed, "n_direct": len(typed), "n_propagated": sum(1 for i in final if final[i]["prop"]),
                    "neighbours": neigh, "neigh_edges": nedges})

# ---------- who is signed in, and what is theirs ----------
# This used to be one JSON file with two arrays and no user at all, so on a public site every
# visitor shared one favourites list and /api/remove_added would delete anyone's uploads.
import auth, store


def need_user():
    """The signed-in user, or None. Browsing needs no account; only these routes do.

    A browser sends the session cookie. A local instance syncing on someone's behalf sends a
    bearer token instead, which is why both are accepted here rather than in each route.
    """
    hdr = request.headers.get("Authorization", "")
    if hdr.startswith("Bearer "):
        return store.user_for_token(hdr[7:].strip())
    return store.get_user(auth.current_sub())


# A synced skill carries neighbour ids, and those are row numbers in skills.json. Sent to an
# instance holding a different corpus they would name different skills — silently. Both sides
# compare this before anything moves.
def corpus_id():
    import hashlib
    if "cid" not in D:
        D["cid"] = hashlib.sha256(
            (str(D["N"]) + "|" + "|".join(r["name"] for r in D["recs"][:400])).encode()
        ).hexdigest()[:16]
    return D["cid"]


@app.get("/api/corpus")
def corpus():
    return jsonify({"id": corpus_id(), "skills": D["N"]})


# A local run borrows skillfabri.com's accounts rather than asking every user to register a
# Google project and a GitHub app of their own. Set SF_UPSTREAM="" to opt out and use your own.
UPSTREAM = os.environ.get("SF_UPSTREAM", "https://skillfabri.com").rstrip("/")
_LINK = ROOT / "data" / "upstream.json"          # token + who it belongs to; gitignored


def link_read():
    try:
        return json.load(open(_LINK))
    except Exception:
        return None


def link_write(d):
    _LINK.parent.mkdir(parents=True, exist_ok=True)
    with open(_LINK, "w") as f:
        json.dump(d, f)
    try:
        os.chmod(_LINK, 0o600)                   # it is a credential
    except Exception:
        pass


def is_upstream():
    """True when this process *is* the service others connect to."""
    return not UPSTREAM or UPSTREAM.endswith("skillfabri.com") and request.host.endswith("skillfabri.com")


@app.get("/auth/cli/callback")
def cli_callback():
    """Where the browser lands after signing in upstream. Exchanges the code for a token."""
    import requests as rq
    code, state = request.args.get("code", ""), request.args.get("state", "")
    if not code or state != _LINK_STATE[0]:
        return "<h3>Sign-in did not complete</h3><p>The state did not match. Close this tab and try again.</p>", 400
    try:
        r = rq.post(f"{UPSTREAM}/api/cli/exchange", timeout=20,
                    json={"code": code, "label": f"local · {os.uname().nodename}"[:60]})
        d = r.json()
    except Exception as e:
        return f"<h3>Could not reach {UPSTREAM}</h3><pre>{esc_html(str(e))}</pre>", 502
    if not d.get("token"):
        return f"<h3>Sign-in failed</h3><pre>{esc_html(str(d))}</pre>", 400
    if d.get("corpus") and d["corpus"] != corpus_id():
        return ("<h3>Connected, but the corpora differ</h3><p>This install and " + UPSTREAM +
                " hold different skill sets, so neighbour ids would not line up. "
                "Sync is disabled.</p>"), 409
    link_write({"token": d["token"], "user": d["user"], "upstream": UPSTREAM})
    _LINK_STATE[0] = ""
    return ("<script>window.close()</script>"
            "<h3>Connected</h3><p>You can close this tab and return to SkillFabri.</p>")


def esc_html(x):
    return (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_LINK_STATE = [""]


@app.post("/api/link/start")
def link_start():
    """Hand the UI the URL to open. The state is kept here and checked on the way back."""
    import secrets as _s
    if is_upstream():
        return jsonify({"error": "this instance is the upstream"}), 400
    _LINK_STATE[0] = _s.token_urlsafe(16)
    cb = f"{request.host_url.rstrip('/')}/auth/cli/callback"
    return jsonify({"url": f"{UPSTREAM}/auth/cli/start?cb={quote(cb, safe='')}"
                           f"&state={quote(_LINK_STATE[0])}", "upstream": UPSTREAM})


@app.post("/api/link/forget")
def link_forget():
    try:
        _LINK.unlink()
    except Exception:
        pass
    return jsonify({"ok": True})


def up_call(method, path, payload=None):
    """Forward a request to the linked account. Returns (json, status) or None if not linked.

    When this install is connected, the account lives upstream — so its saved skills and its
    favourites do too, and reading them out of the local database would show an empty list
    that is not the user's.
    """
    link = link_read()
    if not link:
        return None
    import requests as rq
    try:
        r = rq.request(method, f"{link['upstream']}{path}", timeout=25,
                       headers={"Authorization": "Bearer " + link["token"]}, json=payload)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 502


@app.post("/api/sync")
def sync_up():
    """Push a locally placed skill to the connected account."""
    import requests as rq
    link = link_read()
    if not link:
        return jsonify({"error": "not connected"}), 401
    try:
        r = rq.post(f"{link['upstream']}/api/save", timeout=25,
                    headers={"Authorization": "Bearer " + link["token"]},
                    json=request.get_json(force=True) or {})
        return jsonify({"ok": r.status_code == 200, "status": r.status_code}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.get("/api/me")
def me():
    link = link_read()
    return jsonify({"user": need_user(), "login": auth.enabled(),
                    "providers": auth.providers(), "client_id": auth.CLIENT_ID or None,
                    "upstream": None if is_upstream() else UPSTREAM,
                    "linked": link["user"] if link else None})


@app.post("/api/auth/google")
def auth_google():
    info = auth.verify_google((request.get_json(force=True) or {}).get("credential", ""))
    if not info:
        return jsonify({"error": "invalid token"}), 401
    sub = "google:" + info["sub"]        # namespaced, so two providers cannot collide
    u = store.upsert_user(sub, info.get("email"), info.get("name"), info.get("picture"))
    return auth.issue(jsonify({"ok": True, "user": u}), sub)


def gh_redirect_uri():
    """The callback URL, built from the request so one code path covers every environment.

    The scheme has to come from X-Forwarded-Proto: Fly terminates TLS at the edge, so
    request.url_root reads http, and GitHub rejects the handshake unless this matches the
    registered callback exactly — down to the scheme.
    """
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{proto}://{request.host}/auth/github/callback"


NEXT_COOKIE = "sf_after_auth"


def safe_next(v):
    """Only ever resume at a path on this site. `next` is attacker-supplied."""
    return v if (v or "").startswith("/") and not (v or "").startswith("//") else None


@app.get("/auth/github/start")
def gh_start():
    if not auth.providers()["github"]:
        return redirect("/app#mine")
    url, state = auth.gh_start_url(gh_redirect_uri())
    r = redirect(url)
    r.set_cookie(auth.STATE_COOKIE, state, max_age=600, httponly=True,
                 samesite="Lax", secure=request.is_secure, path="/")
    nxt = safe_next(request.args.get("next"))
    if nxt:
        r.set_cookie(NEXT_COOKIE, nxt, max_age=600, httponly=True,
                     samesite="Lax", secure=request.is_secure, path="/")
    return r


@app.get("/auth/github/callback")
def gh_callback():
    if not auth.gh_check_state(request.cookies.get(auth.STATE_COOKIE), request.args.get("state")):
        return redirect("/app#mine?auth=state")     # the echoed state did not match ours
    prof = auth.gh_exchange(request.args.get("code", ""), gh_redirect_uri())
    if not prof:
        return redirect("/app#mine?auth=failed")
    gid, email, name, avatar = prof
    sub = "github:" + gid
    store.upsert_user(sub, email, name, avatar)
    r = redirect(safe_next(request.cookies.get(NEXT_COOKIE)) or "/app#mine")
    r.delete_cookie(auth.STATE_COOKIE, path="/")
    r.delete_cookie(NEXT_COOKIE, path="/")
    return auth.issue(r, sub)


# A popup 520px wide should not be the whole explorer. This page is the sign-in card and
# nothing else: no corpus, no nav, no graph, and no explorer.html.
CONNECT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in &middot; SkillFabri</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>
:root{color-scheme:dark;--bg:#17171C;--card:#212128;--ink:#ECECF2;--ink2:#A9A9B4;
 --muted:#7A7A86;--line:rgba(255,255,255,.09);--accent:#7A8DF2}
@media(prefers-color-scheme:light){:root{color-scheme:light;--bg:#F8F7F1;--card:#fff;
 --ink:#141414;--ink2:#4D4D4D;--muted:#9A9A9A;--line:rgba(0,0,0,.08);--accent:#2847E0}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);
 color:var(--ink);font-family:Inter,-apple-system,system-ui,sans-serif;padding:22px}
.card{width:100%;max-width:380px;background:var(--card);border:1px solid var(--line);
 border-radius:16px;padding:26px 26px 22px;text-align:center}
h1{font-size:19px;font-weight:800;letter-spacing:-.02em;margin:0 0 6px}
.lede{color:var(--ink2);font-size:14px;line-height:1.55;margin:0 0 20px}
.prov{display:flex;flex-direction:column;align-items:center;gap:10px}
.gh{display:flex;align-items:center;justify-content:center;gap:10px;width:280px;height:40px;
 border-radius:20px;background:#24292F;color:#fff;text-decoration:none;font-size:14px;font-weight:500}
@media(prefers-color-scheme:dark){.gh{background:#30363D}}
.gh:hover{filter:brightness(1.25)}
.hint{color:var(--muted);font-size:11.5px;line-height:1.6;margin:18px 0 0}
.who{margin-top:14px;font-size:13px;color:var(--ink2)}
</style></head><body><div class="card">
<h1>Sign in</h1>
<p class="lede">__WHY__</p>
<div class="prov">
  <div id="g"></div>
  __GH__
</div>
<p class="hint">Name, email and avatar only. Connecting lets skills you place on your machine
 be saved to this account.</p>
</div>
<script>
const CID=__CID__;
function done(){location.reload();}
if(CID){const s=document.createElement('script');s.src='https://accounts.google.com/gsi/client';
 s.async=s.defer=true;s.onload=()=>{google.accounts.id.initialize({client_id:CID,callback:async r=>{
   const q=await fetch('/api/auth/google',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({credential:r.credential})});
   if(q.ok)done();else alert('Sign-in failed.');}});
  google.accounts.id.renderButton(document.getElementById('g'),
   {theme:matchMedia('(prefers-color-scheme: dark)').matches?'filled_black':'outline',
    size:'large',shape:'pill',text:'continue_with',width:280});};
 document.head.appendChild(s);}
</script></body></html>"""


@app.get("/auth/cli/start")
def cli_start():
    """Begin connecting a local instance. Sign in here, come back with a code."""
    cb, state = request.args.get("cb", ""), request.args.get("state", "")
    if not auth.loopback_ok(cb):
        return jsonify({"error": "callback must be a loopback URL with a port"}), 400
    u = need_user()
    if not u:
        here = request.full_path
        gh = (f'<a class="gh" href="/auth/github/start?next={quote(here, safe="")}">'
              '<svg viewBox="0 0 16 16" width="17" height="17" fill="currentColor">'
              '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 '
              '0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 '
              '1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 '
              '0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 0 1 2-.27c.68 0 1.36.09 '
              '2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 '
              '3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/>'
              '</svg>Continue with GitHub</a>') if auth.providers()["github"] else ""
        page = (CONNECT_PAGE
                .replace("__WHY__", "Connect this browser to your SkillFabri account.")
                .replace("__GH__", gh)
                .replace("__CID__", json.dumps(auth.CLIENT_ID or "")))
        return app.response_class(page, mimetype="text/html")
    sep = "&" if "?" in cb else "?"
    return redirect(f"{cb}{sep}code={quote(auth.cli_code(u['sub']))}&state={quote(state)}")


@app.post("/api/cli/exchange")
def cli_exchange():
    """Trade the one-time code for a bearer token. Called by the local process, not a browser."""
    sub = auth.cli_read_code((request.get_json(force=True) or {}).get("code", ""))
    if not sub:
        return jsonify({"error": "code is invalid or expired"}), 400
    u = store.get_user(sub)
    if not u:
        return jsonify({"error": "no such user"}), 404
    label = (request.get_json(force=True) or {}).get("label") or "local instance"
    return jsonify({"token": store.issue_token(sub, label[:60]), "user": u,
                    "corpus": corpus_id()})


@app.post("/api/auth/logout")
def auth_logout():
    return auth.clear(jsonify({"ok": True}))


@app.get("/api/mine")
def mine():
    u = need_user()
    if not u:
        up = up_call("GET", "/api/mine")
        if up:
            return jsonify(up[0]), up[1]
        return jsonify({"error": "sign in", "login": auth.enabled()}), 401
    favs = [slim(D["recs"][i]) for i in store.favourites(u["sub"]) if 0 <= i < D["N"]]
    return jsonify({"added": store.added(u["sub"]), "favorites": favs, "user": u})


@app.post("/api/save")
def save_skill():
    u = need_user()
    if not u:
        return jsonify({"error": "sign in", "login": auth.enabled()}), 401
    b = request.get_json(force=True) or {}
    rec = {"name": b.get("name") or "untitled", "summary": b.get("summary") or "", "steps": b.get("steps") or [],
           "tech": b.get("tech") or [], "activity": b.get("activity") or "—", "capability": b.get("capability") or "—",
           "object": (b.get("labels") or {}).get("object") if b.get("labels") else "—",
           "neighbours": (b.get("neighbours") or [])[:45], "neigh_edges": (b.get("neigh_edges") or [])[:400],
           "labels": b.get("labels"), "ts": b.get("ts") or 0}
    store.add_skill(u["sub"], rec)
    return jsonify({"ok": True})


@app.post("/api/remove_added")
def remove_added():
    u = need_user()
    if not u:
        up = up_call("POST", "/api/remove_added", request.get_json(force=True) or {})
        if up:
            return jsonify(up[0]), up[1]
        return jsonify({"error": "sign in"}), 401
    # the delete is scoped to the owner, so a row id from another account matches nothing
    ok = store.remove_added(u["sub"], int((request.get_json(force=True) or {}).get("id", -1)))
    return jsonify({"ok": ok})


@app.post("/api/fav")
def fav():
    u = need_user()
    if not u:
        up = up_call("POST", "/api/fav", request.get_json(force=True) or {})
        if up:
            return jsonify(up[0]), up[1]
        return jsonify({"error": "sign in", "login": auth.enabled()}), 401
    i = int((request.get_json(force=True) or {}).get("id", -1))
    if not (0 <= i < D["N"]):
        return jsonify({"error": "no such skill"}), 404
    return jsonify({"ok": True, "on": store.toggle_favourite(u["sub"], i)})

# ---------- neighbourhood graph for a skill (center + neighbours + edges among them) ----------
@app.get("/api/graph")
@cached
def graph():
    i = int(request.args.get("id", -1)); minsim = float(request.args.get("minsim", 0.70)); cap = int(request.args.get("cap", 400))
    if i < 0 or i >= D["N"]: return jsonify({"error": "not found"}), 404
    # verified relations at sim >= minsim (plus every same/contain), sorted strongest-first
    rel_ids = {e["id"] for e in D["rel"][i]}
    cands = sorted([e for e in D["rel"][i] if e["type"] in ("same", "contain") or e["sim"] >= minsim], key=lambda e: -e["sim"])
    typed_ids = [e["id"] for e in cands[:cap]]
    # raw semantic candidates (cos >= minsim) that are NOT verified relations -> faint "near" edges, so the
    # slider is meaningful across the whole 0.70-0.95 range (below the ~0.78 relation floor there is still structure).
    # needs profile embeddings; absent -> just the verified-relation graph (no "near" layer).
    near = []; budget = max(0, cap - len(typed_ids))
    if D.get("X") is not None:
     sims = D["X"] @ D["X"][i]
     for j in np.argsort(-sims):
        j = int(j)
        if sims[j] < minsim: break                    # argsort is descending -> everything after is lower
        if j == i or j in rel_ids: continue
        near.append((j, float(sims[j])))
        if len(near) >= budget: break
    # dedupe, order-preserving. A pair can be recorded under two types — `same` and
    # `intersect` on the same edge, for 5,579 pairs — so rel[i] lists that neighbour twice
    # and it used to be drawn as two nodes. layout() is deterministic, so the twins landed
    # exactly on top of each other and only showed up when one was dragged away.
    nodeset = list(dict.fromkeys([i] + typed_ids + [j for j, _ in near]))
    ns = set(nodeset); edges = []; ekey = set()
    for a in nodeset:
        for e in D["rel"][a]:
            b = e["id"]
            if b in ns:
                k = (a, b) if a < b else (b, a)
                if k in ekey: continue
                ekey.add(k); edges.append({"a": a, "b": b, "type": e["type"], "sim": e["sim"], "dir": e["dir"] if e["type"] == "contain" else 0})
    for j, s in near:                                  # center -> each candidate neighbour (no verified relation to center)
        k = (i, j) if i < j else (j, i)
        if k in ekey: continue
        ekey.add(k); edges.append({"a": i, "b": j, "type": "near", "sim": round(s, 3), "dir": 0})
    # keep it readable: all center-star + same/contain edges, then the strongest intersect edges (cap total)
    prio = {"same": 0, "contain": 1, "intersect": 2, "near": 3}
    star = [e for e in edges if e["a"] == i or e["b"] == i]                       # every center↔neighbour edge (all nodes stay connected)
    rest = sorted([e for e in edges if e["a"] != i and e["b"] != i], key=lambda e: (prio[e["type"]], -e["sim"]))
    edges = star + rest[:800]                                                     # among-neighbour edges: keep the strongest (perf)
    nodes = [{"id": k, "name": D["recs"][k]["name"], "activity": D["recs"][k]["activity"], "capability": D["recs"][k]["capability"], "pop": D["recs"][k]["pop"], "sis": (match_steps(i, k) if k != i else [])} for k in nodeset]
    return jsonify({"center": i, "nodes": nodes, "edges": edges})

# ---------- hero constellation: a diverse, popular sample + strong edges among it ----------
@app.get("/api/hero")
@cached
def hero():
    per = int(request.args.get("per", 10)); floor = float(request.args.get("floor", 0.74))
    ids = []
    for a, idxs in D["groups"]["activity"].items(): ids += idxs[:per]      # top-`per` popular per activity
    ns = set(ids); edges = []; ekey = set()
    for a in ids:
        for e in D["rel"][a]:
            b = e["id"]
            if b in ns and e["sim"] >= floor:
                k = (a, b) if a < b else (b, a)
                if k in ekey: continue
                ekey.add(k); edges.append({"a": a, "b": b, "t": e["type"]})
    nodes = [{"id": k, "name": D["recs"][k]["name"], "activity": D["recs"][k]["activity"], "pop": D["recs"][k]["pop"]} for k in ids]
    return jsonify({"nodes": nodes, "edges": edges})

# ---------- label a raw SKILL.md (the real first pipeline stage) ----------
ACTS = ["code-review", "backend-development", "test-automation", "security-audit", "ci-cd-deployment",
        "automation-scripting", "documentation", "debugging", "frontend-development", "project-planning",
        "architecture-design", "performance-optimization", "monitoring-operations", "refactoring",
        "requirements-analysis", "llm-agent-development", "api-database-design", "dependency-management", "data-engineering",
        # build-target domains added in the additive pass — keep in sync with label_primary.ACTIVITIES
        "ui-ux-design", "mobile-development", "web3-blockchain", "ml-engineering",
        "data-visualization", "game-development"]
OBJECTS = ["source-code", "analysis-report", "documentation", "test-suite", "pull-request", "infra-config",
           "backend-service", "ai-agent", "cicd-config", "frontend-ui", "application", "database", "cli-tool",
           "library", "mobile-app", "data-pipeline", "rest-api"]
LABEL_SYS = (f"""You label ONE software-engineering agent skill from its raw SKILL.md, for a knowledge graph. Return ONE JSON:
- fine: the ONE specific FUNCTION, lowercase-kebab, specific & reusable (never 'general'/'misc'; a language/tool is a tag).
- summary: ONE sentence "<action(s)> <subject> using <key tech>, producing <outputs>", <=28 words.
- steps: 4-10 "VERB + object" workflow operations, no filler.
- activity: the single primary activity, EXACTLY ONE of: {', '.join(ACTS)}.
- object: main artifact, EXACTLY ONE of: {', '.join(OBJECTS)}.
- tags: {{"tech":[langs/frameworks/tools], "domain":[business/app domain], "concern":[cross-cutting quality e.g. security/performance]}}.
Return ONLY JSON {{"fine":"","summary":"","steps":[],"activity":"","object":"","tags":{{"tech":[],"domain":[],"concern":[]}}}}.""")

def label_md(md):
    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"], default_headers={"X-Title": "SkillFabri"})
    r = client.chat.completions.create(model="google/gemini-3.7-flash", temperature=0,
        messages=[{"role": "system", "content": LABEL_SYS}, {"role": "user", "content": f"SKILL.md:\n{md[:8000]}\n\nLabel it."}])
    m = re.search(r"\{.*\}", r.choices[0].message.content, re.DOTALL)
    o = json.loads(m.group(0)) if m else {}
    tg = o.get("tags", {}) or {}
    return {"name": o.get("fine") or "", "fine": o.get("fine") or "", "summary": o.get("summary") or "",
            "steps": o.get("steps") or [], "activity": o.get("activity") if o.get("activity") in ACTS else "",
            "object": o.get("object") if o.get("object") in OBJECTS else "",
            "tech": tg.get("tech") or [], "domain": tg.get("domain") or [], "concern": tg.get("concern") or []}

@app.get("/api/full")
@cached
def full():
    i = int(request.args.get("id", -1))
    if i < 0 or i >= D["N"]: return jsonify({"error": "not found"}), 404
    s = D["nodes"][i]; tg = s.get("tags", {}) or {}
    # optional anchor: the skill we opened this modal FROM -> highlight the overlapping workflow step
    anchor = int(request.args.get("anchor", -1))
    hlsteps = match_steps(i, anchor) if 0 <= anchor < D["N"] and anchor != i else []
    aname = D["recs"][anchor]["name"] if 0 <= anchor < D["N"] and anchor != i else ""
    return jsonify({"id": i, "name": s.get("name"), "summary": s.get("summary") or s.get("desc") or "",
        "activity": s.get("primary"), "capability": s.get("capability"), "object": s.get("object"),
        "fine": s.get("fine"), "subject": s.get("subject") or "", "steps": s.get("steps") or [],
        "tech": tg.get("tech") or [], "domain": tg.get("domain") or [], "concern": tg.get("concern") or [],
        "tools": s.get("tools") or [], "input": s.get("input") or [], "output": s.get("output") or [],
        "produces": s.get("produces") or [], "authority": s.get("authority") or [],
        "pop": int(s.get("popularity") or 0), "market": s.get("market") or "", "url": s.get("url") or "",
        "author": s.get("author") or "", "repo": s.get("repo") or "", "repo_url": s.get("repo_url") or "",
        "hlsteps": hlsteps, "anchor_name": aname,
        "counts": dict(collections.Counter(e["type"] for e in D["rel"][i]))})

# `/` is the published brand design, mirrored from the Figma build so the landing page is exactly
# the page that was designed. The data explorer keeps its own route and is linked from it.
HOME = HERE / "home"

# One address for one site. Three hostnames served the same pages, which meant registering three
# JavaScript origins with Google, one callback with GitHub that could only ever be one of them,
# and search engines indexing the same content three times.
CANONICAL = "skillfabri.com"
ALIASES = {"www.skillfabri.com", "skillfabri.fly.dev"}


@app.before_request
def canonical_host():
    """Send the aliases to the canonical host, permanently.

    Matched on the exact hostname rather than a pattern, so Fly's health check — which reaches
    the machine by address, not by name — is never redirected and never fails the deploy.
    """
    host = (request.host or "").split(":")[0].lower()
    if host not in ALIASES:
        return None
    # Fly terminates TLS at the edge, so request.url reads http here. Taking the scheme from
    # X-Forwarded-Proto keeps the hop on https instead of bouncing through http and back.
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    tail = request.full_path if request.query_string else request.path
    return redirect(f"{proto}://{CANONICAL}{tail}", code=301)

@app.get("/")
def home():
    idx = HOME / "index.html"
    return send_file(idx) if idx.exists() else send_file(HERE / "explorer.html")

@app.get("/hydrate.js")
def home_hydrate(): return send_file(HOME / "hydrate.js")

@app.get("/favicon.svg")
def favicon():
    return send_file(HERE / "favicon.svg", mimetype="image/svg+xml")


@app.get("/favicon-32.png")
def favicon_png():
    return send_file(HERE / "favicon-32.png", mimetype="image/png")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_icon():
    return send_file(HERE / "apple-touch-icon.png", mimetype="image/png")


@app.get("/favicon.ico")
def favicon_ico():
    # Browsers probe /favicon.ico regardless of what <link> says, and some of them will not
    # take an SVG. Answer the bare path with the raster so nothing falls through to a 404.
    return send_file(HERE / "favicon-32.png", mimetype="image/png")


@app.get("/icons.js")
def icons(): return send_file(HERE / "icons.js")

@app.get("/assets/<path:fn>")
def home_assets(fn):
    p = (HOME / "assets" / fn).resolve()
    if str(p).startswith(str(HOME)) and p.is_file(): return send_file(p)
    return ("not found", 404)

@app.get("/app")
def explorer_app(): return send_file(HERE / "explorer.html")

@app.get("/<path:fn>")
def static_file(fn):
    p = (HERE / fn).resolve()
    if str(p).startswith(str(HERE)) and p.is_file() and p.suffix in (".html", ".json", ".css", ".js", ".png", ".svg"):
        return send_file(p)
    return ("not found", 404)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--graph", default="relations.json", help="graph file in data/ (A/B a re-typed graph)")
    args = ap.parse_args()
    load(args.graph)
    print(f"[explorer] http://localhost:{args.port}")
    app.run(host="127.0.0.1", port=args.port, threaded=True)
