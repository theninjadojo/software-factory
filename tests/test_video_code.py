import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import urlencode

from factory import codecopy as C
from factory import factorychat as F
from factory import videos
from factory.config import RunnerCfg, overrides_path
from factory.runner import Route, sandbox_cmd
from test_chatvideos import REFS, block
from test_ui import UiCase


def cfg(d: Path, repos=("o/r",), code=True):
    return NS(db_path=str(d / "state" / "factory.db"), repos=list(repos), chat=NS(factory_code=code),
              videos=NS(max_seconds=120))


def make_copy(d: Path, repo="o/r", files=None):
    base = d / "state" / "code" / C.dirname(repo)
    (base / ".git").mkdir(parents=True)
    for rel, text in (files or {}).items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text) if isinstance(text, str) else (base / rel).write_bytes(text)
    return base


class Copies(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "state").mkdir()

    def test_refresh_clones_once_then_waits(self):
        calls = []

        def git(args, cwd, env, timeout=180):
            calls.append(args[0])
            if args[0] == "clone":
                Path(args[-1], ".git").mkdir(parents=True)
            self.assertNotIn("x-access-token", " ".join(args))                    # the token is never on the command line
        c = cfg(self.d)
        self.assertEqual(C.refresh(c, "tok", run_git=git), {})
        self.assertEqual(calls, ["clone"])
        self.assertEqual(C.refresh(c, "tok", run_git=git), {})                    # fresh: nothing to do
        self.assertEqual(calls, ["clone"])
        self.assertEqual(C.mounts(c), [(str(self.d / "state" / "code" / "o__r"), "/work/code/o__r")])
        self.assertIn("/work/code/o__r", C.listing(c))
        self.assertEqual(C.refresh(c, None), {"o/r": "no GitHub token"})

    def test_only_regular_text_files_inside_a_configured_copy_are_read(self):
        base = make_copy(self.d, files={"src/app.py": "".join(f"line {i}\n" for i in range(1, 101)), "bin.dat": b"\0\1\2"})
        os.symlink("/etc/passwd", base / "link")
        c = cfg(self.d)
        name, lines, a, b = C.excerpt(c, "o/r:src/app.py", 10, 90)
        self.assertEqual((name, a, b, len(lines)), ("o/r:src/app.py", 10, 49, 40))       # at most 40 lines
        self.assertEqual(lines[0]["text"], "line 10")
        for bad in ("o/r:../../etc/passwd", "o/r:link", "o/r:.git/config", "o/r:bin.dat", "other/repo:src/app.py", "o/r:missing.py", "nonsense"):
            self.assertIsNone(C.excerpt(c, bad, 1, 5), bad)
        self.assertIsNone(C.excerpt(c, "o/r:src/app.py", 200, 210))                # outside the file
        self.assertIsNone(C.excerpt(cfg(self.d, repos=()), "o/r:src/app.py", 1, 5))   # not a configured repository any more


class Proposals(unittest.TestCase):
    def test_a_proposal_may_name_files_the_copy_has(self):
        ok = lambda f: f == "o/r:src/app.py"
        p = F.parse_proposal(block(tickets=[], files=["o/r:src/app.py"]), REFS, ok)[1]
        self.assertEqual((p["tickets"], p["files"]), ([], ["o/r:src/app.py"]))
        self.assertIsNone(F.parse_proposal(block(tickets=[], files=["o/r:nope.py"]), REFS, ok)[1])
        self.assertIsNone(F.parse_proposal(block(tickets=[], files=[]), REFS, ok)[1])          # neither tickets nor files
        self.assertIsNone(F.parse_proposal(block(files=["o/r:src/app.py"]), REFS, None)[1])    # no code: files are refused

    def test_the_prompt_says_where_the_code_is(self):
        self.assertIn("/work/code/o__r", F.build_prompt("snap", [], code="- o/r: /work/code/o__r\n"))
        self.assertIn("no copy of the code", F.build_prompt("snap", []))


class Writer(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "state").mkdir()
        make_copy(self.d, files={"src/app.py": "def login():\n    return True\n"})

    def test_code_scenes_come_from_the_copy(self):
        text = "```factory-video\n" + json.dumps({"scenes": [
            {"kind": "title", "title": "Login", "say": "How login works."},
            {"kind": "code", "source": "o/r:src/app.py", "from": 1, "to": 2, "say": "The login function."},
            {"kind": "code", "source": "o/r:../secret", "from": 1, "to": 2, "say": "Nope."},
            {"kind": "code", "source": "o/r:src/app.py", "from": "1", "to": 2, "say": "Bad numbers."}]}) + "\n```\n"
        scenes = videos.brief_scenes(text, {"prs": {}, "shots": {}, "tickets": []}, cfg(self.d))
        self.assertEqual([s["kind"] for s in scenes], ["title", "code"])
        self.assertEqual([x["text"] for x in scenes[1]["lines"]], ["def login():", "    return True"])
        self.assertIn("lines 1 to 2", scenes[1]["subtitle"])

    def test_the_writer_prompt_lists_the_code_and_the_files(self):
        row = {"title": "Login", "brief": "How login works.", "files": json.dumps(["o/r:src/app.py"])}
        text = videos.writer_prompt(NS(max_seconds=120), row, {"prs": {}, "shots": {}, "tickets": []}, "- o/r: /work/code/o__r\n")
        self.assertIn('"source": "owner/repo:path/in/repo.py"', text)
        self.assertIn("- o/r:src/app.py", text)
        self.assertNotIn("/work/code", videos.writer_prompt(NS(max_seconds=120), row, {"prs": {}, "shots": {}, "tickets": []}))

    def test_without_a_writer_the_named_files_open_the_video(self):
        row = {"title": "Login", "brief": "How login works.", "files": json.dumps(["o/r:src/app.py"])}
        scenes = videos.fixed_brief(row, {"prs": {}, "shots": {}, "tickets": []}, cfg(self.d))
        self.assertEqual([s["kind"] for s in scenes], ["title", "code"])

    def test_the_code_is_mounted_read_only(self):
        cmd = sandbox_cmd(RunnerCfg(), Route("claude-code", "sonnet", "low"), "n", Path("/w"), ro_mounts=[("/s/code/o__r", "/work/code/o__r")])
        self.assertIn("/s/code/o__r:/work/code/o__r:ro", cmd)


class Page(UiCase):
    def on(self):
        overrides_path(str(self.root / "config.toml")).write_text(
            "[general]\ndry_run = false\n[chat]\nfactory_enabled = true\n[videos]\nenabled = true\n")

    def test_a_video_request_stays_on_the_videos_page_until_it_is_made(self):
        self.on()
        cookie, csrf = self.session()
        db = sqlite3.connect(self.db_path)
        F.ensure_tables(db)
        c = F.new_thread(db, "Propose a video: login")
        F.add_reply(db, {"id": F.add_person(db, "Propose a video: login", c)}, block(tickets=["o/r#3"], files=["o/r:src/app.py"]), None,
                    refs=REFS, file_ok=lambda f: True)
        html = self.req("GET", f"/videos?c={c}", cookie=cookie)[2]
        self.assertIn("Your video request", html)
        self.assertIn("o/r:src/app.py", html)
        self.assertIn('name="in" value="videos"', html)
        tid = F.turns(db, c)[-1]["id"]
        s, h, _ = self.req("POST", "/chat/video/make", urlencode([("csrf", csrf), ("id", tid), ("c", c), ("in", "videos"), ("voice", "")]),
                           cookie=cookie)
        self.assertEqual(h["Location"], f"/videos?c={c}")
        row = videos.briefs(db)[0]
        self.assertEqual(json.loads(row["files"]), ["o/r:src/app.py"])
        self.assertIn("appears in the list below", self.req("GET", h["Location"], cookie=cookie)[2])
        s, h, _ = self.req("POST", "/chat/send", urlencode([("csrf", csrf), ("text", "shorter please"), ("c", c), ("in", "videos")]), cookie=cookie)
        self.assertEqual(h["Location"], f"/videos?c={c}")

    def test_an_unknown_request_shows_the_box(self):
        self.on()
        cookie, _ = self.session()
        self.assertIn('action="/videos/ask"', self.req("GET", "/videos?c=999", cookie=cookie)[2])


if __name__ == "__main__":
    unittest.main()
