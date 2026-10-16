"""Registry-truth purge: drop universe members that do not exist (MC 10738).

Owner ruling 2026-10-15 (MC 10219, option 3 of three priced options): *delete
what does not exist*. The walked universe carried 1,871 members with no bar
file in the store; ``fetch_store`` builds its ``not_fetched`` map from
registry membership alone (``fetch_store.py:76-77``) and ``store_io`` §9 #3
blocks publish on any non-zero count — so phantom membership, not the
network, is what keeps the dashboard dark. Plan:
``.audits/20261015-t34-purge-plan/PLAN.md``; adversarial gate: same dir,
``DA-verdict.md`` (P0-2 -> the exclusion below).

CRITERION (one rule, applied uniformly to EVERY raw bucket incl.
``international``; filefulness is a ticker property because the store is
keyed by ticker alone): a member is purged iff ``cache_filename(member)`` has
no CSV in the store. NO timestamp bump, no reorder, no escape change — the
diff is membership deletions and nothing else (the plan's byte-level check),
which is also what makes a re-run a byte-exact no-op. The input is
re-serialised and compared with its own bytes BEFORE anything is removed: if
the tracked file's serialisation ever changes this refuses, instead of
reformatting 15,000 lines.

ONE EXCLUSION, DERIVED (never a hard-coded list): the NYSE class-share members
whose registry symbol carries ``/`` AND which the in-repo listing directory
``us_tickers.csv`` lists verbatim. T36 probed them live through the repo's own
seam — the ``/`` form dies with ``unexpected character`` while the dash form
in the SAME batch LANDED 251 rows — so their missing CSV is a request-FORM
defect, NOT evidence of non-existence. MC 10761 has since landed that fix
(``9cda546``: send ``BRK-A``, key it back to ``BRK_A.csv``), so their bars are
EXPECTED from the next nightly — but they are not in the store yet, so on the
criterion's own evidence they are fileless today and this exclusion is what
keeps Berkshire Hathaway A+B in the universe. Deleting them to make a guard
read zero is the failure mode it exists to prevent. They stay byte-identical,
and §9 #3 stays red until tonight's fetch proves them; that residual is
reported, not papered over.

NOT in the criterion, deliberately: stale-bar members. ``store_io.STALE_DAYS``
classifies data FRESHNESS, not existence; some stale-but-real members keep the
guard red and STAY (redefining the universe as "companies with fresh quotes"
is an owner decision nobody has taken). ``indicator_stats.json`` is untouched
(the §7 shrink floor has no committed anchor, so it stays dormant).
``refresh_universe.py`` keeps its claim as the sole path to a NEW universe:
this is the registry-TRUTH class of change, precedent ``761e26e``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "source"), str(REPO_ROOT / "source" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from full_universe import CACHE_DIR, REGISTRY_PATH, cache_filename  # noqa: E402

US_DIRECTORY_PATH = REPO_ROOT / "source" / "rocket" / "data" / "us_tickers.csv"
REMOVAL_REASON = "no bar file in source/data/raw (MC 10738 criterion)"
MANIFEST_HEADER = "# region\tindex_in_region\tmember\treason"


def load_us_directory(path: Path = US_DIRECTORY_PATH) -> set[str]:
    """Symbols the in-repo NYSE/NASDAQ listing directory lists verbatim."""
    syms = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh.read().splitlines()[1:]:
            tok = line.split(",")[0].strip().strip('"')
            if tok:
                syms.add(tok)
    return syms


def class_share_exclusions(members: set[str], us_directory: set[str]) -> frozenset:
    """Members the criterion must not touch: ``/`` class-share notation the
    listing directory itself carries. Derived from the directory, so if the
    directory changes this set changes and the test pin goes red for a human
    to read — it cannot silently grow to cover anything else."""
    return frozenset(t for t in members if "/" in t and t in us_directory)


def fileless_members(buckets: dict[str, list[str]], store_dir: Path,
                     keep: frozenset = frozenset()) -> list[str]:
    """Every member of any bucket with no bar file in the store, minus keep."""
    out = {t for v in buckets.values() for t in v
           if not (store_dir / cache_filename(t)).exists()} - set(keep)
    return sorted(out)


def transform(doc: dict, store_dir: Path, *,
              us_directory: set[str] | None = None,
              ) -> tuple[dict, list[dict], frozenset]:
    """One purge pass in memory — pure, writes nothing.

    Returns ``(new_doc, removals, kept_exclusions)``; ``removals`` is one row
    per MEMBERSHIP deleted (a member sitting in three buckets is three rows —
    that is the number §9 #3 moves by), each carrying the bucket and the
    index it occupied, so the pre-purge file is reconstructable from the
    manifest plus the post-purge file.
    """
    buckets = doc["tickers"]
    members = {t for v in buckets.values() for t in v}
    keep = class_share_exclusions(
        members, us_directory if us_directory is not None
        else load_us_directory())
    purged = set(fileless_members(buckets, store_dir, keep))
    removals = [{"region": region, "index": i, "member": t,
                 "reason": REMOVAL_REASON}
                for region, vals in buckets.items()
                for i, t in enumerate(vals) if t in purged]
    new_doc = dict(doc)                                  # key order preserved
    new_doc["tickers"] = {k: [t for t in v if t not in purged]
                          for k, v in buckets.items()}
    return new_doc, removals, keep


def serialise(doc: dict) -> str:
    """The tracked file's exact shape: 2-indent JSON, no trailing newline."""
    return json.dumps(doc, indent=2, ensure_ascii=False)


def manifest_text(removals: list[dict]) -> str:
    rows = [MANIFEST_HEADER] + [
        f"{r['region']}\t{r['index']}\t{r['member']}\t{r['reason']}"
        for r in removals]
    return "\n".join(rows) + "\n"


def run(registry: Path, store_dir: Path, *, out: Path | None = None,
        manifest: Path | None = None) -> int:
    """Purge once. ``out`` defaults to the registry path (in place); the
    store is read by NAME only and is never written — pointing ``--store`` at
    a copy is how the rollback gets rehearsed without touching the live
    store."""
    raw = registry.read_text(encoding="utf-8")
    doc = json.loads(raw)
    if serialise(doc) != raw:
        print("FAIL-CLOSED — tracked registry is not in the shape this "
              "transform serialises; refusing to reformat it", file=sys.stderr)
        return 1
    new_doc, removals, keep = transform(doc, store_dir)
    text = serialise(new_doc)
    target = out or registry
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    if manifest is not None:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(manifest_text(removals), encoding="utf-8")
    uniq_before = {t for v in doc["tickers"].values() for t in v}
    uniq_after = {t for v in new_doc["tickers"].values() for t in v}
    print(f"memberships deleted   : {len(removals)}")
    print(f"unique members removed: {len(uniq_before - uniq_after)}")
    print(f"class-share kept      : {len(keep)} {sorted(keep)}")
    print(f"unique members after  : {len(uniq_after)}")
    print(f"written               : {target}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    ap.add_argument("--store", type=Path, default=CACHE_DIR,
                    help="bar-file store dir, read-only (default the live "
                         "source/data/raw; point it at a copy to rehearse)")
    ap.add_argument("--out", type=Path, default=None,
                    help="write here instead of in place (rehearsal)")
    ap.add_argument("--manifest", type=Path, default=None,
                    help="write the removal manifest here (TSV, committed)")
    args = ap.parse_args()
    return run(args.registry, args.store, out=args.out, manifest=args.manifest)


if __name__ == "__main__":
    sys.exit(main())
