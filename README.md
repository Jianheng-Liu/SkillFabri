# SkillFabri — local

A typed relational graph of the software-engineering agent-skill market, packaged to run on
your own machine. This is the finished graph and the app that serves it — not the pipeline that
built it. For the corpus, the labelling stages and the measurement harness, see the
[full project](https://github.com/Jianheng-Liu/SkillFabri).

Live at **[skillfabri.com](https://skillfabri.com)**. The difference here is that a local run
can place *your* skill in the graph, which the public site deliberately cannot.

## What it does

The agent-skill market is a pile of independently-authored `SKILL.md` files spread across
several marketplaces. Nobody knows what is already in it: the same job is written a dozen times
under a dozen names, and a listing tells you how a skill is advertised, not what it does.

Every skill here was read from its own `SKILL.md` and placed on a capability × object grid,
then linked to every other skill by one of three relations:

- **same** ≡ near-duplicate — the same job, subject and stack
- **contain** ⊑ one skill is a strict superset of the other (directed)
- **intersect** ⊓ the two agree on at least two concrete workflow operations

## Run it

```bash
git clone <this repo>
cd skillfabri-local
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python explorer/app.py --port 8000
# open http://localhost:8000
```

That is enough for the whole site: browse by any dimension, full-text search, and every skill's
interactive relation graph. No key, no account, no extra files.

## Sign in, and keep your own skills

Two things are gated behind an account, because both produce something that has to belong to
somebody: saving a skill from the market, and adding one of your own.

```bash
cp env.sh.example env.sh     # fill in the values below
source env.sh
```

| setting | what it is for |
|---|---|
| `SF_SECRET_KEY` | signs the session cookie — any long random string |
| `SF_GOOGLE_CLIENT_ID` | [Google Cloud console](https://console.cloud.google.com/apis/credentials) → OAuth client ID → **Web application**. Add `http://localhost:8000` to *Authorized JavaScript origins*; leave redirect URIs empty. |
| `SF_GITHUB_CLIENT_ID` / `_SECRET` | [GitHub developer settings](https://github.com/settings/developers) → New OAuth App. Callback URL `http://localhost:8000/auth/github/callback`. |

Either provider alone is enough. Configure neither and the app says sign-in is unavailable
rather than offering a button that cannot work.

Accounts, saved skills and your own uploads live in `data/user.db`, a SQLite file this app
creates. It is gitignored. It never leaves your machine.

## The embeddings

Two files are far too large for git — together over a gigabyte — so they live on the Hugging
Face Hub:

| file | powers |
|---|---|
| `skill_embeddings.npy` | **Add My Skill** — finds your skill's neighbours |
| `step_embeddings.npz` | the **×N** badge — which concrete operations two skills share |

Neither is needed to browse, search, or open a skill's graph. Fetch them when you want what
they power:

```bash
python tools/fetch_data.py                            # both
python tools/fetch_data.py --only skill_embeddings.npy
```

Resumable, and each file is checked against the corpus after it lands — a truncated `.npy` is
still a file, and would otherwise fail much later somewhere confusing.

If you would rather not download, `tools/build_embeddings.py` rebuilds
`skill_embeddings.npy` from `data/skills.json` with an API key. Roughly 14k short texts through
an embedding model: cents, not dollars. `--backend local` uses sentence-transformers and needs
no key at all.

<details><summary>Publishing them yourself</summary>

`huggingface_hub` 1.x dropped `huggingface-cli`; the command is `hf`.

```bash
pip install huggingface_hub
hf auth login                                   # token from huggingface.co/settings/tokens (Write)

hf repo create skillfabri-data --type dataset
hf upload skillfabri-data data/skill_embeddings.npy skill_embeddings.npy --repo-type dataset
hf upload skillfabri-data data/step_embeddings.npz  step_embeddings.npz  --repo-type dataset
```

The third argument is the name *in the repo*, and `tools/fetch_data.py` looks for exactly those
two names. Point it elsewhere with `SF_HF_REPO=you/your-dataset`. The default is
[`JianhengLiu/skillfabri-data`](https://huggingface.co/datasets/JianhengLiu/skillfabri-data).

</details>

## Add your own skill

Placing a skill reads its `SKILL.md`, labels it with an LLM, embeds it, and searches the corpus
for neighbours — so it needs `skill_embeddings.npy` from above, plus a key for the labelling:

```bash
export OPENROUTER_API_KEY=...
```

Restart the server and **Add My Skill** becomes available. Paste or drop a `SKILL.md` and it is
labelled, placed, and saved to your account.

> This is the reason to run it locally. The public site ships without the embeddings and without
> a key — on a public deployment every visitor would be spending the owner's money — so it shows
> these instructions instead.

## What is in here

```
explorer/            the server and the two pages it serves
  app.py             routes, the graph queries, the placement pipeline
  auth.py            Google ID tokens and the GitHub code flow
  store.py           SQLite: accounts, saved skills, uploads
  embedder.py        the embedding client
  explorer.html      the explorer, one file
  home/              the landing page
data/
  skills.json        every labelled skill
  relations.json     the typed graph over them
tools/
  fetch_data.py        pulls the embeddings from Hugging Face
  build_embeddings.py  or rebuilds the profile ones yourself
```

Not here, by design: the crawler, the labelling and taxonomy stages, the relation judging, the
evaluation harness, and the 300 MB raw `SKILL.md` corpus. Those rebuild the graph; this serves
it.

## License

See [`LICENSE`](LICENSE).
