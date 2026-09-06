"""Per-user storage: who is signed in, what they saved, what they added.

Until now this was one JSON file with two arrays and no notion of a user, which on a public
site meant every visitor shared one favourites list and could delete anyone's uploads. It is
SQLite now, one row per (user, skill).

SQLite rather than Postgres because the app already runs as a single machine with a single
worker — that is forced by memory, since each worker holds its own copy of the parsed corpus.
The one real cost of SQLite is that it cannot be shared across machines, and we have already
paid that. A managed Postgres would add a service and about $5/month to buy nothing.

The file lives on a Fly volume in production (SF_DB=/data/user.db). Without one it falls back
to data/user.db, which is fine locally and lost on redeploy in a container — the volume is
what makes it durable.
"""
import json, os, sqlite3, threading, time
from pathlib import Path

_LOCK = threading.Lock()
_DB = None


def db_path() -> Path:
    p = os.environ.get("SF_DB")
    return Path(p) if p else Path(__file__).resolve().parent.parent / "data" / "user.db"


def _conn():
    global _DB
    if _DB is None:
        p = db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # one connection reused across threads; every write goes through _LOCK
        _DB = sqlite3.connect(str(p), check_same_thread=False)
        _DB.row_factory = sqlite3.Row
        _DB.execute("PRAGMA journal_mode=WAL")      # a reader never blocks the writer
        # With more than one gunicorn worker the writers are separate processes, so the
        # threading lock above no longer covers them — SQLite's own lock does. Without a
        # busy timeout the loser of a race raises "database is locked" immediately instead
        # of waiting the few milliseconds the other write needs.
        _DB.execute("PRAGMA busy_timeout=5000")
        _DB.execute("PRAGMA foreign_keys=ON")
        _init(_DB)
    return _DB


def _init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      sub     TEXT PRIMARY KEY,          -- Google's stable subject id, not the email
      email   TEXT,
      name    TEXT,
      picture TEXT,
      created INTEGER NOT NULL,
      seen    INTEGER NOT NULL);

    CREATE TABLE IF NOT EXISTS favourites(
      sub      TEXT    NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
      skill_id INTEGER NOT NULL,
      created  INTEGER NOT NULL,
      PRIMARY KEY(sub, skill_id));

    CREATE TABLE IF NOT EXISTS added(
      id      INTEGER PRIMARY KEY AUTOINCREMENT,
      sub     TEXT    NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
      payload TEXT    NOT NULL,          -- the placed-skill record, as JSON
      created INTEGER NOT NULL);

    CREATE INDEX IF NOT EXISTS fav_by_user   ON favourites(sub, created DESC);
    CREATE INDEX IF NOT EXISTS added_by_user ON added(sub, created DESC);
    """)
    c.commit()
    _namespace_subjects(c)


def _namespace_subjects(c):
    """Prefix bare subject ids with their provider.

    The first version only had Google, so it stored the raw `sub`. With a second provider those
    ids share a namespace and could in principle collide, so they become `google:<sub>` and
    `github:<id>`. Google's subs are numeric and GitHub's ids are integers, so a value with no
    colon is unambiguously an old Google row.

    Foreign keys are off for the rewrite: updating users.sub while favourites still point at the
    old value would violate the constraint mid-migration.
    """
    if not c.execute("SELECT 1 FROM users WHERE sub NOT LIKE '%:%' LIMIT 1").fetchone():
        return
    c.execute("PRAGMA foreign_keys=OFF")
    try:
        for t in ("users", "favourites", "added"):
            c.execute(f"UPDATE {t} SET sub='google:'||sub WHERE sub NOT LIKE '%:%'")
        c.commit()
        print("[store] migrated existing rows to provider-prefixed subject ids")
    finally:
        c.execute("PRAGMA foreign_keys=ON")


def now() -> int:
    return int(time.time())


# ---------- users ----------
def upsert_user(sub, email, name, picture):
    with _LOCK:
        c = _conn()
        c.execute("""INSERT INTO users(sub,email,name,picture,created,seen)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(sub) DO UPDATE SET
                       email=excluded.email, name=excluded.name,
                       picture=excluded.picture, seen=excluded.seen""",
                  (sub, email, name, picture, now(), now()))
        c.commit()
    return get_user(sub)


def get_user(sub):
    if not sub:
        return None
    r = _conn().execute("SELECT sub,email,name,picture FROM users WHERE sub=?", (sub,)).fetchone()
    return dict(r) if r else None


# ---------- favourites ----------
def favourites(sub):
    """Skill ids this user saved, most recent first."""
    return [r["skill_id"] for r in _conn().execute(
        "SELECT skill_id FROM favourites WHERE sub=? ORDER BY created DESC", (sub,))]


def toggle_favourite(sub, skill_id) -> bool:
    """Returns True if the skill is now saved, False if it was removed."""
    with _LOCK:
        c = _conn()
        hit = c.execute("SELECT 1 FROM favourites WHERE sub=? AND skill_id=?",
                        (sub, skill_id)).fetchone()
        if hit:
            c.execute("DELETE FROM favourites WHERE sub=? AND skill_id=?", (sub, skill_id))
            on = False
        else:
            c.execute("INSERT INTO favourites(sub,skill_id,created) VALUES(?,?,?)",
                      (sub, skill_id, now()))
            on = True
        c.commit()
    return on


# ---------- skills the user placed themselves ----------
def added(sub):
    out = []
    for r in _conn().execute("SELECT id,payload FROM added WHERE sub=? ORDER BY created DESC", (sub,)):
        try:
            rec = json.loads(r["payload"])
        except Exception:
            continue
        rec["_id"] = r["id"]                 # the row id, so deletes do not depend on list order
        out.append(rec)
    return out


def add_skill(sub, rec) -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute("INSERT INTO added(sub,payload,created) VALUES(?,?,?)",
                        (sub, json.dumps(rec, ensure_ascii=False), now()))
        c.commit()
        return cur.lastrowid


def remove_added(sub, row_id) -> bool:
    """Scoped to the owner: a row id from someone else's account matches nothing."""
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM added WHERE id=? AND sub=?", (row_id, sub))
        c.commit()
        return cur.rowcount > 0


def stats():
    c = _conn()
    q = lambda s: c.execute(s).fetchone()[0]
    return {"users": q("SELECT COUNT(*) FROM users"),
            "favourites": q("SELECT COUNT(*) FROM favourites"),
            "added": q("SELECT COUNT(*) FROM added")}
