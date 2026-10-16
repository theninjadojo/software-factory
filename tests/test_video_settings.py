import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from factory import videoimage as VI
from factory.config import RunnerCfg, VideosCfg, load
from factory.ui import settings as S
from test_ui_admin import AdminCase


def cfg(**over):
    return NS(runner=RunnerCfg(), videos=VideosCfg(**over), dry_run=False)


class Engine:
    def __init__(self, build_ok=True, images=()):
        self.build_ok, self.images, self.calls = build_ok, set(images), []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        code, err = 0, ""
        if cmd[1:3] == ["image", "inspect"]:
            code = 0 if cmd[3] in self.images else 1
        elif cmd[1] == "build":
            code, err = (0, "") if self.build_ok else (1, "npm ERR! network")
            if code == 0:
                self.images.add(cmd[cmd.index("-t") + 1])
        elif cmd[1] == "tag":
            self.images.add(cmd[3])
        return subprocess.CompletedProcess(cmd, code, "", err)


class Build(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())

    def tick(self, c, eng):
        return VI.tick(c, self.state, eng, spawn=lambda f: f())

    def test_a_request_builds_a_candidate_then_moves_the_tag(self):
        eng = Engine()
        VI.request(self.state)
        self.assertTrue(self.tick(cfg(), eng))
        self.assertFalse(VI.requested(self.state))
        builds = [c for c in eng.calls if c[1] == "build"]
        self.assertEqual(builds[0][builds[0].index("-t") + 1], "localhost/factory-video:candidate")
        self.assertIn(["podman", "tag", "localhost/factory-video:candidate", VI.DEFAULT], eng.calls)
        st = VI.read(self.state)
        self.assertTrue(st["last"]["ok"])
        self.assertTrue(st["exists"])
        self.assertFalse(st["building"])

    def test_a_failed_build_keeps_the_image_and_says_why(self):
        eng = Engine(build_ok=False)
        VI.request(self.state)
        self.tick(cfg(), eng)
        st = VI.read(self.state)
        self.assertFalse(st["last"]["ok"])
        self.assertIn("npm ERR", st["last"]["message"])
        self.assertFalse([c for c in eng.calls if c[1] == "tag"])

    def test_a_custom_image_is_never_built(self):
        eng = Engine()
        VI.request(self.state)
        self.tick(cfg(image="registry.example/video:1"), eng)
        self.assertFalse([c for c in eng.calls if c[1] == "build"])
        self.assertIn("custom", VI.read(self.state)["last"]["message"])

    def test_without_a_request_it_only_checks_the_image_now_and_then(self):
        eng = Engine(images={VI.DEFAULT})
        self.assertTrue(self.tick(cfg(), eng))
        self.assertTrue(VI.read(self.state)["exists"])
        self.assertFalse(self.tick(cfg(), eng))                     # checked a moment ago
        self.assertFalse([c for c in eng.calls if c[1] == "build"])

    def test_dry_run_does_nothing(self):
        eng = Engine()
        VI.request(self.state)
        c = cfg()
        c.dry_run = True
        self.assertFalse(self.tick(c, eng))
        self.assertEqual(eng.calls, [])


class Page(AdminCase):
    def test_the_section_saves_every_video_setting_to_the_overrides(self):
        cookie, csrf = self.session()
        s, _, html = self.req("GET", "/settings?section=videos", cookie=cookie)
        self.assertEqual(s, 200)
        for needle in ("Enable review videos", "Default voice", "ElevenLabs voice id", "Videos per day", "Video image", "ElevenLabs key"):
            self.assertIn(needle, html)
        form = {"section": "videos", "videos.enabled": "1", "videos.voice": "piper", "videos.elevenlabs_voice_id": "21m00Tcm4TlvDq8ikWAM",
                "videos.elevenlabs_model": "eleven_multilingual_v2", "videos.max_per_day": "4", "videos.max_seconds": "90", "videos.max_mb": "50",
                "videos.retention_days": "30", "videos.timeout_seconds": "600", "videos.ui_url": "", "videos.image": VI.DEFAULT}
        s, h, _ = self.post(cookie, csrf, "/settings/save", form)
        self.assertEqual(s, 303, h)
        v = load(str(self.root / "config.toml")).videos
        self.assertEqual((v.enabled, v.max_per_day, v.max_seconds, v.elevenlabs_voice_id), (True, 4, 90, "21m00Tcm4TlvDq8ikWAM"))

    def test_a_bad_voice_id_is_refused_on_the_page(self):
        cookie, csrf = self.session()
        s, _, html = self.post(cookie, csrf, "/settings/save", {"section": "videos", "videos.voice": "elevenlabs", "videos.elevenlabs_voice_id": "",
                                                                  "videos.max_per_day": "10", "videos.max_seconds": "120", "videos.max_mb": "50",
                                                                  "videos.retention_days": "30", "videos.timeout_seconds": "600"})
        self.assertEqual(s, 422)
        self.assertIn("elevenlabs_voice_id", html)

    def test_build_button_only_writes_a_request_and_not_in_dry_run(self):
        cookie, csrf = self.session()
        self.assertEqual(self.req("POST", "/videos/image/build", "", cookie=cookie)[0], 403)        # no CSRF token
        if load(str(self.root / "config.toml")).dry_run:
            self.post(cookie, csrf, "/videos/image/build")
            self.assertFalse(VI.requested(self.state))
            S.set_dry_run(str(self.root / "config.toml"), self.state, False)
        s, h, _ = self.post(cookie, csrf, "/videos/image/build")
        self.assertEqual((s, h["Location"]), (303, "/settings?section=videos"))
        self.assertTrue(VI.requested(self.state))

    def test_the_elevenlabs_key_is_saved_from_the_section_and_never_shown(self):
        cookie, csrf = self.session()
        s, h, _ = self.post(cookie, csrf, "/credentials/save", {"name": "elevenlabs", "value": "sk_test_secret_value_123", "back": "videos"})
        self.assertEqual((s, h["Location"]), (303, "/settings?section=videos"))
        html = self.req("GET", "/settings?section=videos", cookie=cookie)[2]
        self.assertIn("ElevenLabs key saved", html)
        self.assertNotIn("sk_test_secret_value_123", html)
        self.assertIn("Replace the key", html)
        s, _, html = self.post(cookie, csrf, "/credentials/save", {"name": "elevenlabs", "value": "short", "back": "videos"})
        self.assertEqual(s, 400)
        self.assertIn("Enable review videos", html)                 # stays on the section

    def test_the_settings_home_lists_review_videos_with_a_switch(self):
        cookie, csrf = self.session()
        html = self.req("GET", "/settings", cookie=cookie)[2]
        self.assertIn("Review videos", html)
        self.assertIn('value="videos.enabled"', html)
        self.post(cookie, csrf, "/settings/feature", {"key": "videos.enabled", "on": "1", "back": "/settings"})
        self.assertTrue(load(str(self.root / "config.toml")).videos.enabled)


if __name__ == "__main__":
    unittest.main()
