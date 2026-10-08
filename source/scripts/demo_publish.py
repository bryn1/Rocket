"""Stage-6 publish mechanics per DESIGN §6a — sync BEFORE write, nothing
parked.

The two served roots (index.html, indicator_stats.json) are regenerable
nightly; this module is the ONLY git-publishing path of the nightly. Steps:
1 self-clear rebase residue · 2 restore crash residue · 2b discard
race-loser regenerable commits (DA-c3 C3-F2) · 3 pull --rebase (fail-closed
nothing written) · 4 write + certify markers on bytes READ BACK FROM DISK ·
5 two-path add + commit + push (loud on rejection) · 6 ls-remote equality
with ONE transport retry (PUBLISH-UNPROVEN network / PUBLISH-DIVERGED).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CONFLICT_MARKERS = ("<<<<<<<", ">>>>>>>")
REGENERABLE_ROOTS = ("index.html", "indicator_stats.json")


def _fail(msg: str) -> None:
    print(f"FAIL-CLOSED — {msg}", file=sys.stderr)
    sys.exit(1)


def _abort_rebase_residue(repo_root: Path) -> None:
    """§6a #1: a stranded rebase dir best-effort aborts; STILL present -> exit
    1 naming it (the common strand path self-heals nightly; never a brick)."""
    for name in ("rebase-merge", "rebase-apply"):
        out = subprocess.run(["git", "rev-parse", "--git-path", name],
                             cwd=repo_root, capture_output=True, text=True,
                             check=True).stdout.strip()
        path = Path(out) if Path(out).is_absolute() else repo_root / out
        if not path.exists():
            continue
        subprocess.run(["git", "rebase", "--abort"], cwd=repo_root,
                       check=False)
        if path.exists():
            _fail(f"git-rebase-rester ({name}) kunde inte avbrytas — "
                  f"mate in manuellt")


def _discard_race_loser_commits(repo_root: Path) -> None:
    """§6a step 2b (DA-c3 C3-F2, T13 F1): a lost push race leaves a local
    commit whose next rebase conflicts FOREVER (both sides rewrote index.html
    from the same parent). After the fetch, commits in bryn1/main..HEAD whose
    diff against the MERGE-BASE touches ONLY the two regenerable roots ->
    reset --hard bryn1/main (they regenerate nightly, step 2's own premise) +
    DISCARDED-LOCAL per commit; ANY foreign path -> exit 1 naming it, hands
    off. The touched set is merge-base..HEAD, NEVER tip-to-tip: a tip-to-tip
    diff enumerates the REMOTE's own newer files as differences (HEAD lacks
    them) and bricks every following night (DA T9 F1)."""
    subprocess.run(["git", "fetch", "bryn1", "main"], cwd=repo_root,
                   check=True)
    log = subprocess.run(
        ["git", "log", "--format=%H\t%s", "bryn1/main..HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True).stdout
    if not log.strip():
        return
    base = subprocess.run(
        ["git", "merge-base", "bryn1/main", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True).stdout.strip()
    touched = subprocess.run(
        ["git", "diff", "--name-only", base, "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True).stdout.split()
    foreign = [p for p in touched if p not in REGENERABLE_ROOTS]
    if foreign:
        _fail(f"lokala commits före bryn1/main rör {', '.join(foreign)} — "
              f"mate in manuellt")
    for line in log.strip().splitlines():
        sha, subject = line.split("\t", 1)
        print(f"DISCARDED-LOCAL {sha} {subject}", file=sys.stderr)
    subprocess.run(["git", "reset", "--hard", "bryn1/main"], cwd=repo_root,
                   check=True)


def _certify_on_disk(index_path: Path, markers: list[str]) -> None:
    """§6a #4 content certification on the bytes READ BACK FROM DISK: the full
    stage-5 marker list re-runs + conflict markers absent, BEFORE staging."""
    disk = index_path.read_text(encoding="utf-8")
    bad = [m for m in markers if m not in disk]
    bad += [c for c in CONFLICT_MARKERS if c in disk]
    if bad:
        _fail(f"read-back certifiering: {', '.join(bad)}")


def _lsremote_proven(repo_root: Path) -> str:
    """§6a #6: sha == bryn1/main after ONE transport retry, else loud:
    PUBLISH-UNPROVEN network (transport died twice) / PUBLISH-DIVERGED."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                          capture_output=True, text=True, check=True
                          ).stdout.strip()
    last_err = "transport dog"
    for attempt in (1, 2):
        proc = subprocess.run(["git", "ls-remote", "bryn1", "main"],
                              cwd=repo_root, capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            sha = proc.stdout.split()[0]
            if sha == head:
                return head
            print(f"PUBLISH-DIVERGED remote={sha} head={head}",
                  file=sys.stderr)
            sys.exit(1)
        last_err = (proc.stderr or "").strip() or last_err
        if attempt == 1:
            print("ls-remote dog — försöker en gång till", file=sys.stderr)
    print(f"PUBLISH-UNPROVEN network: {last_err}", file=sys.stderr)
    sys.exit(1)


def publish(html_text: str, stats_text: str | None, markers: list[str], *,
            index_path: Path, stats_path: Path,
            repo_root: Path) -> str:
    """§6a — NOTHING is written before the sync; the written bytes ARE the
    final bytes. Returns the proven sha; prints PUBLISHED <sha> only when
    ls-remote equality holds. Every uncertified path exits 1 (last-good
    page stays live)."""
    _abort_rebase_residue(repo_root)                                   # 1
    subprocess.run(["git", "checkout", "--", *REGENERABLE_ROOTS],     # 2
                   cwd=repo_root, check=True)
    _discard_race_loser_commits(repo_root)                             # 2b
    if subprocess.run(["git", "pull", "--rebase", "bryn1", "main"],     # 3
                      cwd=repo_root, check=False).returncode != 0:
        subprocess.run(["git", "rebase", "--abort"], cwd=repo_root,
                       check=False)
        _fail("git sync failed (inget skrivet, last-good lever)")
    index_path.write_text(html_text, encoding="utf-8")                 # 4
    if stats_text is not None:
        stats_path.write_text(stats_text, encoding="utf-8")
    _certify_on_disk(index_path, markers)
    subprocess.run(["git", "add", *REGENERABLE_ROOTS], cwd=repo_root,   # 5
                   check=True)                     # seam C5: exactly these two
    subprocess.run(
        ["git", "-c", "user.name=code (MC 10220)",
         "-c", "user.email=code@agent-town.local", "commit", "-m",
         "publish: regenerate demo page + indicator stats (MC 10220)"],
        cwd=repo_root, check=True)
    subprocess.run(["git", "push", "bryn1", "HEAD:main"], cwd=repo_root,   # 5
                   check=True)
    sha = _lsremote_proven(repo_root)                                  # 6
    print(f"PUBLISHED {sha}")
    return sha
