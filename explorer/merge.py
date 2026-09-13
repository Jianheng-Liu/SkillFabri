"""Synthesize one installable SKILL.md from two skills that share concrete operations.

This is the only part of the site that writes rather than reads, and the design here is the
result of three rounds that failed on measurement rather than on taste. Both failures are
worth keeping written down, because both look like obvious improvements:

  * Truncating the sources to 4,500 characters silently cut 57.5% of the corpus. Uncapped
    inputs retained 100% of the sources' commands against 27% for truncated ones, and cost
    $0.002 more per pair. Output dominates the bill, not input.
  * Leading with the pipeline's extracted steps and asking the model to trace against them
    made the summary the spine and the document the background: step coverage hit 8/8 while
    only 38% of the sources' commands survived. The steps are gone. The document is all
    there is, which is why this module reads SKILL.md rather than skills.json.

`carried` is the mechanism that made detail survive. The model lists the exact strings it
took from each source, and they are checked against both that source and its own output.
Retention went from 38% to 97% when the claim became verifiable. Asking is not enough.
"""
from __future__ import annotations
import json, os, re, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "data" / "skillmd.zip"          # fetched, not committed; see tools/fetch_data.py
MIN_SHARED = 3

_z = None


def available() -> dict:
    """What is missing, so the UI can say which one rather than just 'unavailable'."""
    return {"docs": DOCS.exists(),
            "api_key": bool(os.environ.get("OPENROUTER_API_KEY"))}


def ready() -> bool:
    return all(available().values())


def _docs():
    """The bundle, opened once, or None if this instance does not have it.

    None rather than an exception: the deployment ships without the documents on purpose, and
    the panel that explains that has to be able to ask about a pair first. Raising here made
    every such question a 500, so the explanation never appeared and the button looked broken.
    """
    global _z
    if _z is None:
        if not DOCS.exists():
            return None
        try:
            _z = zipfile.ZipFile(DOCS)
        except (zipfile.BadZipFile, OSError):
            return None                      # half-downloaded, and no more useful than absent
    return _z


def source(i: int) -> str:
    """One skill's own SKILL.md, in full. Read on demand: a merge touches two of twelve
    thousand, and holding them all would cost 100 MB for a feature most readers never use."""
    z = _docs()
    if z is None:
        return ""
    try:
        return z.read(f"{int(i)}.md").decode("utf-8", "ignore")
    except (KeyError, OSError):
        return ""


def eligible(shared, a: int, b: int) -> bool:
    """Enough shared operations to be one skill, and both documents on hand.

    `shared` is the number already on the relation row, passed in rather than looked up. The
    badge and this gate have to be the same number: a Merge button that appears on x 2 or
    refuses on x 4 is the site disagreeing with itself in front of the reader. There was a
    second count here once, from a side table, and it disagreed in both directions.
    """
    return isinstance(shared, int) and shared >= MIN_SHARED and bool(source(a)) and bool(source(b))


SYS = """You merge two software-engineering agent skills into one, and you write the merged
SKILL.md itself.

You get each skill's source document in full. That document is the fact: its preconditions,
the order steps must happen in, the exact commands, the flags, the file paths, what to do when
something fails. Everything you need is in there and nothing outside it is admissible.

Write ONE skill that does what both do, without saying anything twice.

  * Find the operations both documents describe. Each becomes ONE step, stated once, at the
    point in the workflow where it belongs.
  * Keep every command, flag, path and threshold verbatim. Where the two disagree, keep both
    and say which applies when.
  * Keep what is specific to one side. A rule that names a repository, a language or a tool
    is the reason that skill exists; dropping it to make the merge tidy destroys the thing
    being merged.
  * Write it as an operator would follow it, not as a summary of two documents.

Return JSON only:
{
  "mergeable": true|false,
  "why": "one sentence; if false, what makes them not one skill",
  "name": "kebab-case",
  "summary": "one sentence",
  "subject": "...", "object": "...", "tech": ["..."],
  "steps": ["the merged workflow, one short line per operation, in the order they happen"],
  "skill_md": "the full merged SKILL.md, with YAML frontmatter",
  "carried": ["exact strings you took from either source, verbatim"],
  "dropped": ["anything from either source you did not carry, and why"]
}

`steps` is the same workflow as the document, said in one line each. It is what the graph
draws, so it has to match what you wrote rather than summarise it more loosely.

`carried` is checked against both sources and against your own output. List the strings you
actually took: commands, flags, paths, thresholds. Do not paraphrase them."""


def check(out: dict, a_src: str, b_src: str) -> dict:
    """Verify the model's own claims. This is what the retention number means.

    A string the model says it carried must appear in a source and in its output. Claims that
    fail either test are counted separately: one is a merge that dropped detail, the other is
    a citation to something nobody wrote.
    """
    md = out.get("skill_md") or ""
    carried = [c for c in (out.get("carried") or []) if isinstance(c, str) and c.strip()]
    def norm(s): return re.sub(r"\s+", " ", s).strip().lower()
    A, B, M = norm(a_src), norm(b_src), norm(md)
    verified = not_in_source = not_in_output = 0
    for c in carried:
        n = norm(c)
        if not n:
            continue
        in_src = n in A or n in B
        in_out = n in M
        if in_src and in_out:
            verified += 1
        else:
            not_in_source += 0 if in_src else 1
            not_in_output += 0 if in_out else 1
    return {"carried_verified": verified,
            "carried_not_in_source": not_in_source,
            "carried_not_in_output": not_in_output,
            "retention": round(verified / len(carried), 3) if carried else None,
            "md_chars": len(md),
            "md_frontmatter": md.lstrip().startswith("---"),
            "md_sections": len(re.findall(r"^#{1,3} ", md, re.M))}


def run(a_rec: dict, b_rec: dict, a_id: int, b_id: int, model: str, effort: str):
    """Call the model and return (result, usage). Raises on transport failure."""
    from openai import OpenAI
    a_src, b_src = source(a_id), source(b_id)
    if not (a_src and b_src):
        raise RuntimeError("one of the source documents is missing from data/skillmd.zip")
    user = (f"SKILL A — {a_rec.get('name','')}\n\n{a_src}\n\n"
            f"{'=' * 60}\n\nSKILL B — {b_rec.get('name','')}\n\n{b_src}")
    client = OpenAI(base_url="https://openrouter.ai/api/v1",
                    api_key=os.environ["OPENROUTER_API_KEY"],
                    default_headers={"X-Title": "SkillFabri"})
    kw = {"model": model, "temperature": 0,
          "messages": [{"role": "system", "content": SYS},
                       {"role": "user", "content": user}],
          "response_format": {"type": "json_object"}}
    if effort:
        kw["extra_body"] = {"reasoning": {"effort": effort}}
    r = client.chat.completions.create(**kw)
    out = json.loads(r.choices[0].message.content)
    out["_check"] = check(out, a_src, b_src)
    u = getattr(r, "usage", None)
    out["_usage"] = {"in": getattr(u, "prompt_tokens", 0), "out": getattr(u, "completion_tokens", 0)}
    return out
