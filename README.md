# SkillFabri

A typed relational graph of the software-engineering agent-skill market, running on your
machine.

The market is a pile of independently-authored `SKILL.md` files spread across several
marketplaces, and nobody knows what is already in it. The same job is written a dozen times
under a dozen names, and a listing tells you how a skill is advertised, not what it does.

Every skill here was read from its own `SKILL.md` — its activity, capability, object and the
concrete operations it performs — and then linked to every other skill by one of three
relations:

| | | |
|---|---|---|
| **same** | ≡ | near-duplicate: the same job, subject and stack |
| **contain** | ⊑ | one skill is a strict superset of the other (directed) |
| **intersect** | ⊓ | the two agree on at least two concrete workflow operations |

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python explorer/app.py --port 8000
# http://localhost:8000
```

That is the whole thing: browse by activity, capability, object, concern, domain or tool;
full-text search; and every skill's interactive relation graph, with the operations it shares
with each neighbour. No key, no account, no downloads.

## Sign in

Click **Sign in → Continue with skillfabri.com**. A window opens, you sign in with Google or
GitHub, it closes, and you are connected. There is nothing to register and nothing to
configure.

Signing in gets you two things — skills you save from the market, and skills you add yourself —
kept on your account rather than in this copy, so they are there from any machine.

<details><summary>How that works, and how to avoid it</summary>

The browser returns to `http://localhost:<port>` carrying a short-lived code, which this
process trades for a token — the loopback flow `gh auth login` uses. The token lives in
`data/upstream.json` (mode 600, gitignored); only its hash is kept server-side.

To keep everything on this machine instead, set `SF_UPSTREAM=""` and bring your own provider:

| setting | |
|---|---|
| `SF_SECRET_KEY` | any long random string; signs the session cookie |
| `SF_GOOGLE_CLIENT_ID` | [Google Cloud console](https://console.cloud.google.com/apis/credentials) → OAuth client ID → **Web application**. Add `http://localhost:8000` to *Authorized JavaScript origins*; leave redirect URIs empty. |
| `SF_GITHUB_CLIENT_ID` / `_SECRET` | [GitHub developer settings](https://github.com/settings/developers) → New OAuth App, callback `http://localhost:8000/auth/github/callback` |

Either provider alone is enough. Accounts then live only in `data/user.db`.

</details>

## Add your own skill

This is the reason to run it yourself. Paste or drop a `SKILL.md` and it is read, labelled,
embedded, and placed among its neighbours — you label nothing by hand.

It needs two things browsing does not:

```bash
python tools/fetch_data.py --only skill_embeddings.npy   # 226 MB, to find the neighbours
export OPENROUTER_API_KEY=...                            # to read and label the file
```

Restart, and **Add My Skill** appears. Signed in, the result goes to your account.

## The embedding files

Two arrays are far too large for git, so they are fetched from the
[Hub](https://huggingface.co/datasets/JianhengLiu/skillfabri-data):

| file | size | what stops working without it |
|---|--:|---|
| `skill_embeddings.npy` | 226 MB | Add My Skill |
| `step_embeddings.npz` | 1.1 GB | the **×N** badge — which operations two skills share |

```bash
python tools/fetch_data.py              # both
```

Resumable, and each file is checked against the corpus as it lands — a truncated array is still
a file, and would otherwise fail much later somewhere unhelpful.

`tools/build_embeddings.py` computes `skill_embeddings.npy` locally instead of downloading it:
roughly 14k short texts through an embedding model, cents rather than dollars, or
`--backend local` for no key at all.

<details><summary>Publishing your own copy of the arrays</summary>

`huggingface_hub` 1.x replaced `huggingface-cli` with `hf`.

```bash
hf auth login
hf repo create my-data --type dataset
hf upload my-data data/skill_embeddings.npy skill_embeddings.npy --repo-type dataset
hf upload my-data data/step_embeddings.npz  step_embeddings.npz  --repo-type dataset
```

The third argument is the name inside the repo, and `tools/fetch_data.py` looks for exactly
those two. Point it at yours with `SF_HF_REPO=you/my-data`.

</details>

## Layout

```
explorer/
  app.py             routes, the graph queries, placing a new skill
  auth.py            sign-in
  store.py           SQLite: accounts, saved skills, your own uploads
  embedder.py        the embedding client
  explorer.html      the explorer, one file
  home/              the landing page
data/
  skills.json        every labelled skill
  relations.json     the typed graph over them
tools/
  fetch_data.py      pulls the embedding arrays
  build_embeddings.py
```

## License

See [`LICENSE`](LICENSE).
