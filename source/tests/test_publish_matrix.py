"""T5 (MC 10224) §6a publish RED matrix — demo_publish driven as importable
functions against a FAKE REMOTE git (git init --bare in tmp, clone, commit +
push baseline; the remote is always named `bryn1`, real git, zero network).
The green paths and the stubbed step-order pins live in test_demo_publish.py;
this file is the race/planted-state reds (a)-(e) of DESIGN §9/§6a, each
proved with nothing of ours reaching the remote on a refusal.
"""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "scripts"))

import demo_publish                                    # noqa: E402
import demo_render                                     # noqa: E402
from demo_page_fixtures import html as render_html     # noqa: E402

IDENT = ["-c", "user.name=code (MC 10224)",
         "-c", "user.email=code@agent-town.local",
         "-c", "commit.gpgsign=false"]
OLD_PAGE = "OLD PAGE — last-good published demo\n"
HTML = render_html()
STATS_JSON = json.dumps({"schema_version": 1})
MARKERS = [demo_render.BANNER_MARKER, demo_render.TAB_ALL_MARKER,
           demo_render.INDICATOR_TAB_MARKER, demo_render.PANEL_ALL_MARKER,
           demo_render.PANEL_PREFIX + 'usa"']


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          capture_output=True, text=True)


def _commit_all(cwd, msg):
    _git(cwd, *IDENT, "add", "-A")
    _git(cwd, *IDENT, "commit", "-m", msg)


@pytest.fixture
def env(tmp_path):
    """Bare `bryn1` remote + one clean work clone with a committed, pushed
    baseline (everything under tmp; git defaultBranch/gpgsign pinned)."""
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    _git(tmp_path, *IDENT, "clone", str(remote), str(work))
    _git(work, "remote", "rename", "origin", "bryn1")
    (work / "index.html").write_text(OLD_PAGE, encoding="utf-8")
    (work / "indicator_stats.json").write_text('{"old": 1}', encoding="utf-8")
    _commit_all(work, "baseline")
    _git(work, "push", "bryn1", "HEAD:main")
    return SimpleNamespace(tmp=tmp_path, remote=remote, work=work,
                           rival_n=0)


def _publish(env, html=HTML):
    return demo_publish.publish(html, STATS_JSON, MARKERS,
                                index_path=env.work / "index.html",
                                stats_path=env.work / "indicator_stats.json",
                                repo_root=env.work)


def _remote_show(env, path):
    return _git(env.tmp, "--git-dir", str(env.remote), "show",
                f"main:{path}").stdout


def _remote_log(env):
    return _git(env.tmp, "--git-dir", str(env.remote), "log", "--format=%s",
                "main").stdout.splitlines()


def _rival_push(env, text="RIVAL PAGE\n"):
    """Someone else's night lands on the remote (second clone -> push)."""
    env.rival_n += 1
    rival = env.tmp / f"rival{env.rival_n}"
    _git(env.tmp, *IDENT, "clone", str(env.remote), str(rival))
    (rival / "index.html").write_text(text, encoding="utf-8")
    _commit_all(rival, "rival night wins the race")
    _git(rival, "push", "origin", "HEAD:main")


def _rival_repo_commit(env):
    """T13 F1 (MC 10264): the routine history event — bryn1/main advances with
    a NON-root commit (docs/code/registry refresh)."""
    env.rival_n += 1
    rival = env.tmp / f"rival{env.rival_n}"
    _git(env.tmp, *IDENT, "clone", str(env.remote), str(rival))
    (rival / "docs").mkdir()
    (rival / "docs" / "advance.md").write_text("routine advance\n",
                                               encoding="utf-8")
    _commit_all(rival, "routine repo commit (non-root)")
    _git(rival, "push", "origin", "HEAD:main")


def _after_git(monkeypatch, hook):
    """Let demo_publish's git calls run for REAL, then call hook(argv, res)
    — the seam that moves the remote between the publish steps."""
    real = subprocess.run

    def run(argv, *a, **kw):
        res = real(argv, *a, **kw)
        hook(list(argv), res)
        return res
    monkeypatch.setattr(demo_publish.subprocess, "run", run)


# ── (a) remote moves BETWEEN sync and push → loud rejection, nothing lands ──

def test_a_remote_moves_after_sync_push_rejected_nothing_pushed(env,
                                                                 monkeypatch,
                                                                 capsys):
    fired = []

    def hook(argv, res):
        if argv[:2] == ["git", "pull"] and res.returncode == 0 and not fired:
            fired.append(1)
            _rival_push(env)                       # race: remote moves now
    _after_git(monkeypatch, hook)
    with pytest.raises(subprocess.CalledProcessError):
        _publish(env)          # step-5 push fails LOUD (non-zero for systemd)
    out = capsys.readouterr()
    assert fired and "PUBLISHED" not in out.out
    assert _remote_log(env) == ["rival night wins the race", "baseline"]
    assert _remote_show(env, "index.html") == "RIVAL PAGE\n"   # marker-free
    assert 'id="panel-usa"' not in _remote_show(env, "index.html")


# ── (b) planted failed rebase: self-clear next night · un-clearable refuses ─

def test_b_failed_rebase_residue_self_clears_next_night(env, capsys):
    _rival_push(env)                               # remote moved overnight
    (env.work / "index.html").write_text(HTML, encoding="utf-8")
    _commit_all(env.work, "publish: regenerate demo page (night N)")
    r = _git(env.work, "pull", "--rebase", "bryn1", "main", check=False)
    assert r.returncode != 0                       # planted FAILED rebase…
    assert (env.work / ".git" / "rebase-merge").exists()
    sha = _publish(env)                            # …NEXT simulated night
    captured = capsys.readouterr()
    assert not (env.work / ".git" / "rebase-merge").exists()   # self-cleared
    assert "DISCARDED-LOCAL" in captured.err       # stranded night-N commit
    assert f"PUBLISHED {sha}" in captured.out      # green, no brick
    assert 'id="panel-usa"' in _remote_show(env, "index.html")


def test_b_unclearable_rebase_residue_refuses_and_names(env, capsys):
    (env.work / ".git" / "rebase-merge").mkdir()   # garbage no abort can heal
    with pytest.raises(SystemExit) as e:
        _publish(env)
    assert e.value.code == 1
    assert "git-rebase-rester" in capsys.readouterr().err
    assert (env.work / "index.html").read_text(encoding="utf-8") == OLD_PAGE
    assert _remote_log(env) == ["baseline"]        # nothing written/pushed


# ── (c) crash residue: dirty regenerable roots are healed by step 2 ─────────

def test_c_dirty_crash_residue_healed_run_green(env, capsys):
    (env.work / "index.html").write_text("CRASHED between write and push",
                                         encoding="utf-8")
    (env.work / "indicator_stats.json").write_text("{ truncated",
                                                   encoding="utf-8")
    sha = _publish(env)
    assert f"PUBLISHED {sha}" in capsys.readouterr().out
    assert (env.work / "index.html").read_text(encoding="utf-8") == HTML
    assert _git(env.work, "status", "--porcelain").stdout.strip() == ""
    assert 'id="panel-usa"' in _remote_show(env, "index.html")


# ── (d) ls-remote proof: forced sha≠HEAD · transport death twice ────────────

def test_d_forced_sha_mismatch_publish_diverged_exit1(env, monkeypatch,
                                                      capsys):
    other = env.tmp / "other.git"                  # bare with unrelated main
    _git(env.tmp, "init", "--bare", "-b", "main", str(other))
    side = env.tmp / "side"
    _git(env.tmp, *IDENT, "clone", str(other), str(side))
    (side / "f.txt").write_text("other universe\n", encoding="utf-8")
    _commit_all(side, "foreign main")
    _git(side, "push", "origin", "HEAD:main")

    def hook(argv, res):
        if argv[:4] == ["git", "push", "bryn1", "HEAD:main"] and \
                res.returncode == 0:               # our push DID land…
            _git(env.work, "remote", "set-url", "bryn1", str(other))
            # …but the proving remote now answers a foreign sha
    _after_git(monkeypatch, hook)
    with pytest.raises(SystemExit) as e:
        _publish(env)
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "PUBLISH-DIVERGED remote=" in err
    assert "PUBLISH-UNPROVEN" not in err           # distinct red classes


def test_d_transport_death_twice_publish_unproven_network(env, monkeypatch,
                                                          capsys):
    seen: list[list] = []

    def hook(argv, res):
        seen.append(argv)
        if argv[:4] == ["git", "push", "bryn1", "HEAD:main"] and \
                res.returncode == 0:
            _git(env.work, "remote", "set-url", "bryn1",
                 str(env.tmp / "gone.git"))        # transport dies outright
    _after_git(monkeypatch, hook)
    with pytest.raises(SystemExit) as e:
        _publish(env)
    captured = capsys.readouterr()
    assert e.value.code == 1
    # COUNT the calls, not the copy: the retry note prints even if the loop
    # collapses to one attempt — only a real SECOND ls-remote proves §6a #6
    # (mutation-checked: dropping ONE retry from the loop goes RED here):
    assert sum(a[:2] == ["git", "ls-remote"] for a in seen) == 2
    assert "försöker en gång till" in captured.err
    assert "PUBLISH-UNPROVEN network" in captured.err
    assert "PUBLISH-DIVERGED" not in captured.err  # message stays distinct


# ── (e) push-race loser: night N regenerable commits discarded; foreign red ─

def test_e_race_loser_regenerable_commit_discarded_then_green(env, capsys):
    """(e) STRENGTHENED (T13 F1, MC 10264): the race-loser state beside a
    routine NON-root remote advance — the shipping case. The discard test must
    survive `bryn1/main` having moved with files HEAD lacks: the touched-set
    is merge-base..HEAD, never tip-to-tip (DA T9 F1: tip-to-tip read the
    REMOTE's own files as foreign -> exit 1 every night, forever)."""
    (env.work / "index.html").write_text("night N page (push died)\n",
                                         encoding="utf-8")
    (env.work / "indicator_stats.json").write_text('{"night": "N"}',
                                                   encoding="utf-8")
    _commit_all(env.work, "publish: regenerate demo page + stats (night N)")
    lost = _git(env.work, "rev-parse", "HEAD").stdout.strip()
    _rival_repo_commit(env)                        # remote ALSO moved (non-root)
    sha = _publish(env)                            # night N+1
    captured = capsys.readouterr()
    assert f"DISCARDED-LOCAL {lost}" in captured.err
    assert f"PUBLISHED {sha}" in captured.out
    assert _git(env.tmp, "--git-dir", str(env.remote), "rev-parse",
                "main").stdout.strip() == sha      # remote == our proven sha
    assert 'id="panel-usa"' in _remote_show(env, "index.html")
    # hands-off: the remote's own non-root file survives untouched
    assert _remote_show(env, "docs/advance.md") == "routine advance\n"


def test_e2_race_loser_plus_remote_advance_foreign_still_named(env, capsys):
    """(e2) T13 F1 pair: local-ahead commit touches a FOREIGN path WHILE the
    remote also advanced (non-root) -> still exit 1 naming the LOCAL foreign
    path, and never the remote's own newer file (merge-base..HEAD only)."""
    (env.work / "index.html").write_text("night N page (push died)\n",
                                         encoding="utf-8")
    (env.work / "source").mkdir()
    (env.work / "source" / "notes.md").write_text("hand edit\n",
                                                  encoding="utf-8")
    _commit_all(env.work, "human edit ahead of bryn1/main")
    _rival_repo_commit(env)                        # remote advanced, non-root
    with pytest.raises(SystemExit) as e:
        _publish(env)
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "source/notes.md" in err                # the LOCAL foreign path named
    assert "docs/advance.md" not in err            # the REMOTE's file is not foreign
    assert _remote_log(env) == ["routine repo commit (non-root)", "baseline"]
    assert (env.work / "index.html").read_text(encoding="utf-8") == \
        "night N page (push died)\n"               # nothing written: hands off


def test_e_local_commit_with_foreign_path_refuses_naming_it(env, capsys):
    (env.work / "source").mkdir()
    (env.work / "source" / "notes.md").write_text("hand edit\n",
                                                  encoding="utf-8")
    _commit_all(env.work, "human edit ahead of bryn1/main")
    with pytest.raises(SystemExit) as e:
        _publish(env)
    assert e.value.code == 1
    assert "source/notes.md" in capsys.readouterr().err
    assert _remote_log(env) == ["baseline"]        # hands off: nothing pushed
    assert (env.work / "index.html").read_text(encoding="utf-8") == OLD_PAGE
