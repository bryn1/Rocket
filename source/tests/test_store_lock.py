"""MC 11187 (D8-R1 fix, C4): store-writer concurrency + publisher lock +
READ-ONLY --dry-run. Three new guards, one subject each:
  (a) store_io._atomic_write uses a PER-WRITER unique temp (mkstemp in the
      same dir + os.replace) — the old fixed shared ``<name>.csv.tmp`` let
      one writer's os.replace rename another's in-flight temp (torn files,
      parseable-but-wrong duplicate-date stores, D8 R1).
  (b) the generate_demo_page entrypoint takes a NON-BLOCKING flock on
      ``.demo-publish.lock`` in the store root; held => loud "another run
      in progress", exit 1, zero writes.
  (c) --dry-run is READ-ONLY against the store: run_fetch(...,
      writes_enabled=False) fetches and records facts but never upserts.
Every store here is SCRATCH (tmp_path); the live source/data is never
opened. Planted-bad reds: against the pre-fix code these tests fail —
racing writers error/duplicate, the entrypoint never refuses, and
--dry-run mutates the store (red run recorded on the MC 11187 trail).
"""
import fcntl
import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import excluded_tickers  # noqa: E402
import fetch_store  # noqa: E402
import full_universe  # noqa: E402
import fu_fixtures as fu_fx  # noqa: E402
import rocket.data.bulk_fetcher as bf  # noqa: E402
import store_io  # noqa: E402
import test_demo_page_generator as dtgen  # noqa: E402 (_wire_main reuse)
from demo_page_fixtures import NOW, PLAN, mk_frame as _mk_frame  # noqa: E402

gen = dtgen.gen
RUN_NOW = fu_fx.local_evening_utc(fu_fx.today(), "usa")


def _dir_state(d: Path) -> dict:
    """name -> (sha256, mtime_ns, size): byte- AND touch-identity in one
    snapshot (the DoD's mtime+sha dry-run assert)."""
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[p.name] = (hashlib.sha256(p.read_bytes()).hexdigest(),
                           st.st_mtime_ns, st.st_size)
    return out


# ── (a) concurrent production upserts on ONE scratch ticker store ──────────

def test_concurrent_upserts_no_errors_no_duplicate_dates(tmp_path):
    errors: list[str] = []
    start = threading.Barrier(3)

    def writer(tid: int) -> None:
        start.wait()                          # all three racing at once
        for _ in range(20):
            try:
                store_io.upsert("RACER", "usa",
                                fu_fx.mk_frame(40, end=fu_fx.day_delta(-1 - tid)),
                                cache_dir=tmp_path, now_fn=RUN_NOW)
            except Exception as exc:  # noqa: BLE001 — collected, asserted below
                errors.append(f"t{tid}: {exc!r}")

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == []                       # PRE-FIX RED: FileNotFoundError
    #     "<name>.csv.tmp -> <name>.csv" on the shared fixed tmp name
    final = store_io.read_store("RACER", "usa", cache_dir=tmp_path)
    assert final is not None                  # a torn file would read None
    dates = list(pd.to_datetime(final["date"]).dt.date)
    assert len(dates) == len(set(dates))      # PRE-FIX RED: torn merge left
    #     duplicate-date rows that classify() never heals
    residue = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert residue == []                      # swap consumed every temp


# ── (c) dry-run read-only: the run_fetch writes-enabled seam ───────────────

def test_run_fetch_writes_disabled_leaves_store_untouched(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(bf, "BATCH_DELAY", 0)
    plan = full_universe.UniversePlan(regions={"usa": ["A", "B"]},
                                      order=["usa"], m_unique=2)
    for t in ("A", "B"):
        store_io.upsert(t, "usa", fu_fx.mk_frame(10), cache_dir=tmp_path,
                        replace=True, now_fn=RUN_NOW)
    before = _dir_state(tmp_path)

    def fetcher(batch, period):
        return ({t: fu_fx.mk_frame(12, end=fu_fx.day_delta(-1))
                 for t in batch}, {})

    facts = fetch_store.run_fetch(plan, skip_fetch=False, now_fn=RUN_NOW,
                                  cache_dir=tmp_path, fetcher=fetcher,
                                  writes_enabled=False)
    assert all(f["batch_completed"] for f in facts.values())   # facts ride
    assert _dir_state(tmp_path) == before     # PRE-FIX RED: no such flag —
    #     a TypeError at best; the store moved on every rehearsal
    # green control (this check CAN go red): the same walk with writes
    # enabled moves the store, so the identity above is not always-true.
    fetch_store.run_fetch(plan, skip_fetch=False, now_fn=RUN_NOW,
                          cache_dir=tmp_path, fetcher=fetcher)
    assert _dir_state(tmp_path) != before


def test_main_dry_run_store_byte_identical(tmp_path, monkeypatch):
    """Whole entrypoint over the REAL fetch stage (store_io.CACHE_DIR is its
    only store seam); scoring/indicators/publish stay fixture seams — they
    never touch the store."""
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(store_io, "CACHE_DIR", store)
    monkeypatch.setattr(bf, "BATCH_DELAY", 0)
    for r, ts in PLAN.regions.items():
        for t in ts:
            store_io.upsert(t, r, _mk_frame(), cache_dir=store,
                            replace=True, now_fn=lambda: NOW)
    monkeypatch.setattr(bf, "fetch_batch_typed",
                        lambda batch, period: ({t: _mk_frame() for t in batch},
                                                {}))
    monkeypatch.setattr(bf, "typed_capture_state", lambda: "stub")
    monkeypatch.setattr(excluded_tickers, "save",
                        lambda state, path=None: None)  # state file off-limits
    dtgen._wire_main(monkeypatch, tmp_path)
    monkeypatch.setattr(gen, "fetch_store", fetch_store.run_fetch)

    monkeypatch.setattr(sys, "argv", ["gen", "--dry-run"])
    before = _dir_state(store)
    gen.main()                                # green dry pass, no SystemExit
    assert _dir_state(store) == before        # PRE-FIX RED: --dry-run fetched
    #     AND upserted the real store (D8 R1: "even a safe rehearsal run is
    #     a second store writer")
    monkeypatch.setattr(sys, "argv", ["gen"])  # control: real run publishes
    gen.main()                                # (publish seam faked by _wire_main)
    assert _dir_state(store) != before        # red-capable direction proven


def test_main_writes_flag_follows_dry_run(tmp_path, monkeypatch):
    seen: list = []
    dtgen._wire_main(monkeypatch, tmp_path)

    def spy(plan, **kw):
        seen.append(kw.get("writes_enabled"))
        return dtgen._facts_done()

    monkeypatch.setattr(gen, "fetch_store", spy)
    monkeypatch.setattr(sys, "argv", ["gen", "--skip-fetch", "--dry-run"])
    gen.main()
    monkeypatch.setattr(sys, "argv", ["gen", "--skip-fetch"])
    gen.main()
    # B0 (MC 11545): stage 1 makes TWO seam calls per run — bar walk first,
    # index feed second; both follow the same dry/real flag.
    assert seen == [False, False, True, True]  # dry READ-ONLY, real writes


# ── (b) publisher flock: held by another process ⇒ refuse, zero writes ─────

def test_flock_held_by_other_process_refuses_second(tmp_path):
    lock = tmp_path / ".demo-publish.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import fcntl, os, time; fd = os.open({str(lock)!r}, "
         "os.O_CREAT | os.O_RDWR, 0o644); fcntl.flock(fd, fcntl.LOCK_EX); "
         "print('HELD', flush=True); time.sleep(120)"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "HELD"
        env = {**os.environ, "ROCKET_STORE_LOCK": str(lock)}
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "generate_demo_page.py"),
             "--skip-fetch"], cwd=str(gen.REPO_ROOT / "source"), env=env,
            capture_output=True, text=True, timeout=60)
        assert r.returncode != 0
        assert "another run in progress" in r.stderr   # PRE-FIX RED: no lock,
        #     this run reached the registry instead (its stderr names
        #     "registry", never a lock)
        assert "registry" not in r.stderr              # refused BEFORE stage 0
        assert sorted(p.name for p in tmp_path.iterdir()) == [lock.name]
    finally:
        holder.terminate()
        holder.wait()


def test_flock_refusal_is_the_first_action(tmp_path, monkeypatch, capsys):
    """Zero-writes proof at the seam level: every later stage is a live
    detonator — reaching ANY of them raises AssertionError, not SystemExit."""
    lock = tmp_path / ".demo-publish.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)   # a SECOND open file
    fcntl.flock(fd, fcntl.LOCK_EX)              # description => it contends,
    #     exactly like the nightly holding it while a manual run starts
    monkeypatch.setenv("ROCKET_STORE_LOCK", str(lock))
    monkeypatch.setattr(gen, "_lock_fd", None)  # fresh acquisition attempt

    def detonate(*a, **k):
        raise AssertionError("stage reached — the lock must refuse first")

    monkeypatch.setattr(gen, "load_universe_plan", detonate)
    monkeypatch.setattr(gen, "fetch_store", detonate)
    monkeypatch.setattr(sys, "argv", ["gen", "--dry-run"])
    with pytest.raises(SystemExit) as e:
        gen.main()
    assert e.value.code == 1
    assert "another run in progress" in capsys.readouterr().err
    assert not (tmp_path / "dry").exists()      # nothing written
    os.close(fd)                                # release (guard's red above;
    #     green control: every _wire_main main() test walks past acquisition)
