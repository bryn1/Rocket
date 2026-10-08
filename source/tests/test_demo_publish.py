"""demo_publish (MC 10223 §6a + DA-c3 C3-F2 step 2b): git is stubbed — the
tests pin the STEP ORDER (sync-before-write, no stash), the 2b discard
happy path, and the loud uncertified paths. The full publish RED matrix
(live-race attacks 1-4) is the T5 file; these are the happy paths + the
cheap named reds my changes must keep honest.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import demo_publish  # noqa: E402
from demo_page_fixtures import html as _html  # noqa: E402


def _fake_git(monkeypatch, script=()):
    argvs = []

    def fake_run(argv, cwd=None, capture_output=False, text=False,
                 check=False, **kw):
        argvs.append(list(argv))
        for match, res in script:
            if match(argv):
                return SimpleNamespace(returncode=res.get("rc", 0),
                                       stdout=res.get("out", ""),
                                       stderr=res.get("err", ""))
        if argv[:3] == ["git", "rev-parse", "--git-path"]:
            # real git ALWAYS prints the path; it just does not exist here
            return SimpleNamespace(returncode=0,
                                   stdout=f".git/{argv[-1]}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(demo_publish.subprocess, "run", fake_run)
    return argvs


def _base_script(head="abc123"):
    return (
        (lambda a: a[:2] == ["git", "log"], {"out": ""}),
        (lambda a: a == ["git", "rev-parse", "HEAD"], {"out": head + "\n"}),
        (lambda a: a[:2] == ["git", "ls-remote"],
         {"out": f"{head}\trefs/heads/main\n"}),
    )


def test_publish_sync_before_write_no_stash(tmp_path, monkeypatch, capsys):
    argvs = _fake_git(monkeypatch, _base_script())
    sha = demo_publish.publish(
        _html(), '{"a": 1}', ['id="freshness"', 'id="panel-all"'],
        index_path=tmp_path / "index.html",
        stats_path=tmp_path / "indicator_stats.json",
        repo_root=tmp_path)
    assert sha == "abc123" and "PUBLISHED abc123" in capsys.readouterr().out
    assert not any("stash" in a for a in argvs), "stash retired (C2-F1)"
    # self-clear (rev-parse --git-path) before restore; restore before fetch;
    # fetch (2b) before pull; NOTHING writes before the pull (sync-before-
    # write): add/commit/push all follow pull, ls-remote last.
    def pos(pred, what, start=0):
        for i in range(start, len(argvs)):
            if pred(argvs[i]):
                return i
        raise AssertionError(f"missing step {what}: {argvs}")
    p_clear = pos(lambda a: a[:2] == ["git", "rev-parse"]
                  and a[-1] in ("rebase-merge", "rebase-apply"), "self-clear")
    p_restore = pos(lambda a: a[:2] == ["git", "checkout"]
                    and "index.html" in a, "restore", p_clear)
    p_fetch = pos(lambda a: a[:2] == ["git", "fetch"], "fetch", p_restore)
    p_pull = pos(lambda a: a[:4] == ["git", "pull", "--rebase", "bryn1"],
                 "pull", p_fetch)
    p_add = pos(lambda a: a[:2] == ["git", "add"], "add", p_pull)
    p_commit = pos(lambda a: "commit" in a, "commit", p_add)
    p_push = pos(lambda a: a == ["git", "push", "bryn1", "HEAD:main"],
                 "push", p_commit)
    pos(lambda a: a[:2] == ["git", "ls-remote"], "ls-remote", p_push)
    assert (tmp_path / "index.html").read_text() == _html()   # final bytes


def test_publish_2b_discards_regenerable_only(tmp_path, monkeypatch, capsys):
    # T13 F1: the touched-set command is pinned as merge-base..HEAD — the
    # retired tip-to-tip form (bryn1/main HEAD) read the REMOTE's own newer
    # files as foreign; this matcher only fires on the merge-base sha.
    script = ((lambda a: a[:2] == ["git", "log"],
               {"out": "deadbeef\tpublish: regenerate demo page (MC 10220)\n"}),
              (lambda a: a[:2] == ["git", "merge-base"], {"out": "cafe0001\n"}),
              (lambda a: a[:4] == ["git", "diff", "--name-only", "cafe0001"],
               {"out": "index.html\nindicator_stats.json\n"}),
              *_base_script())
    argvs = _fake_git(monkeypatch, script)
    demo_publish.publish(
        _html(), None, ['id="freshness"', 'id="panel-all"'],
        index_path=tmp_path / "index.html",
        stats_path=tmp_path / "indicator_stats.json", repo_root=tmp_path)
    err = capsys.readouterr().err
    assert "DISCARDED-LOCAL deadbeef publish: regenerate demo page" in err
    assert ["git", "reset", "--hard", "bryn1/main"] in argvs
    assert ["git", "push", "bryn1", "HEAD:main"] in argvs      # night N+1 green


def test_publish_2b_foreign_path_exits_naming_it(tmp_path, monkeypatch, capsys):
    # T13 F1: foreign detection reads merge-base..HEAD (see discard-test pin).
    script = ((lambda a: a[:2] == ["git", "log"], {"out": "cafe1\tmine\n"}),
              (lambda a: a[:2] == ["git", "merge-base"], {"out": "cafe0001\n"}),
              (lambda a: a[:4] == ["git", "diff", "--name-only", "cafe0001"],
               {"out": "index.html\nsource/app.py\n"}))
    argvs = _fake_git(monkeypatch, script)
    with pytest.raises(SystemExit) as e:
        demo_publish.publish(
            _html(), None, ['id="freshness"'],
            index_path=tmp_path / "index.html",
            stats_path=tmp_path / "indicator_stats.json", repo_root=tmp_path)
    assert e.value.code == 1
    assert "source/app.py" in capsys.readouterr().err
    assert not any(a[:2] == ["git", "add"] for a in argvs)     # hands off


def test_publish_certifies_read_back_bytes(tmp_path, monkeypatch):
    argvs = _fake_git(monkeypatch, _base_script())
    with pytest.raises(SystemExit):
        demo_publish.publish(                     # marker absent on the bytes
            "<html>offentlig</html>", None, ['id="freshness"'],
            index_path=tmp_path / "index.html",
            stats_path=tmp_path / "indicator_stats.json", repo_root=tmp_path)
    assert not any(a[:2] == ["git", "add"] for a in argvs)     # before staging


def test_publish_diverged_and_unproven(tmp_path, monkeypatch, capsys):
    script = ((lambda a: a[:2] == ["git", "log"], {"out": ""}),
              (lambda a: a == ["git", "rev-parse", "HEAD"], {"out": "abc123\n"}),
              (lambda a: a[:2] == ["git", "ls-remote"],
               {"out": "999999\trefs/heads/main\n"}))
    _fake_git(monkeypatch, script)
    with pytest.raises(SystemExit):
        demo_publish.publish(
            _html(), None, ['id="freshness"'],
            index_path=tmp_path / "index.html",
            stats_path=tmp_path / "indicator_stats.json", repo_root=tmp_path)
    assert "PUBLISH-DIVERGED" in capsys.readouterr().err

    dead = ((lambda a: a[:2] == ["git", "log"], {"out": ""}),
            (lambda a: a == ["git", "rev-parse", "HEAD"], {"out": "abc123\n"}),
            (lambda a: a[:2] == ["git", "ls-remote"],
             {"rc": 128, "out": "", "err": "ssh: connect died"}))
    argvs = _fake_git(monkeypatch, dead)
    with pytest.raises(SystemExit):
        demo_publish.publish(
            _html(), None, ['id="freshness"'],
            index_path=tmp_path / "index.html",
            stats_path=tmp_path / "indicator_stats.json", repo_root=tmp_path)
    err = capsys.readouterr().err
    assert "PUBLISH-UNPROVEN network" in err
    assert sum(a[:2] == ["git", "ls-remote"] for a in argvs) == 2  # ONE retry
