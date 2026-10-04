import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import db as dbm
from factory import health
from factory import main as m
from factory import slack as S
from factory.config import _slack_id
from factory.telegram import parse_callback

ME, CHAN = "U0AAAAAAA", "C0BBBBBBB"


class FakeWS:
    def __init__(self, incoming=()):
        self.incoming, self.sent = list(incoming), []

    def recv(self):
        return self.incoming.pop(0)

    def send(self, text):
        self.sent.append(json.loads(text))


class Base(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.d = self._d.name
        self.db = f"{self.d}/f.db"
        dbm.connect(self.db).close()
        self.sl = S.Slack("xoxb-t", "xapp-t", CHAN, ME, self.d, self.db, ["o/r"])
        self.addCleanup(self._d.cleanup)

    def approvals(self):
        c = dbm.connect(self.db)
        try:
            return dbm.approvals(c)
        finally:
            c.close()

    def unknown(self):
        c = dbm.connect(self.db)
        try:
            return json.loads((dbm.get_status(c).get("slack_unknown_senders") or {}).get("value", "[]"))
        finally:
            c.close()


def click(user, channel, value):
    return {"type": "block_actions", "user": {"id": user, "username": "someone"}, "channel": {"id": channel},
            "actions": [{"action_id": "b0_0", "value": value}]}


class Authorized(unittest.TestCase):
    def test_needs_the_configured_user_and_for_buttons_the_channel(self):
        self.assertTrue(S.authorized(ME, "D1", ME))
        self.assertTrue(S.authorized(ME, CHAN, ME, CHAN))
        self.assertFalse(S.authorized("U0EVIL", CHAN, ME, CHAN))
        self.assertFalse(S.authorized(ME, "C0OTHER", ME, CHAN))
        self.assertFalse(S.authorized(None, CHAN, ME, CHAN))
        self.assertFalse(S.authorized("", CHAN, ""))              # an unset user id never matches an empty one


class Blocks(unittest.TestCase):
    def test_buttons_become_actions_and_their_values_survive_parse_callback(self):
        b = S.blocks("hello", [[("Run", "run|o/r|7"), ("Skip", "skip|o/r|7")], [("Open in UI", "url:https://ui.example/x")]])
        self.assertEqual(b[0]["text"], {"type": "plain_text", "text": "hello", "emoji": False})
        run, skip = b[1]["elements"]
        self.assertEqual(parse_callback(run["value"]), ("run", "o/r", 7))
        self.assertEqual(parse_callback(skip["value"]), ("skip", "o/r", 7))
        link = b[2]["elements"][0]
        self.assertEqual(link["url"], "https://ui.example/x")
        self.assertNotIn("value", link)
        ids = [e["action_id"] for blk in b[1:] for e in blk["elements"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_flat_row_is_one_row(self):
        b = S.blocks("t", [("A", "run|o/r|1"), ("B", "skip|o/r|1")])
        self.assertEqual(len(b), 2)
        self.assertEqual(len(b[1]["elements"]), 2)

    def test_long_text_and_labels_are_cut_to_slacks_limits(self):
        b = S.blocks("x" * 9000, [[("L" * 200, "run|o/r|1")]])
        self.assertEqual(len(b[0]["text"]["text"]), 3000)
        self.assertEqual(len(b[1]["elements"][0]["text"]["text"]), 75)


class Send(Base):
    def test_posts_plain_text_to_the_channel_without_unfurling(self):
        with mock.patch.object(self.sl, "_api") as api:
            self.sl.send("y" * 9000, [[("Run", "run|o/r|1")]])
        method, token = api.call_args.args
        kw = api.call_args.kwargs
        self.assertEqual((method, token), ("chat.postMessage", "xoxb-t"))
        self.assertEqual((kw["channel"], kw["mrkdwn"], kw["unfurl_links"]), (CHAN, False, False))
        self.assertEqual(len(kw["text"]), 3000)
        self.assertIn("blocks", kw)

    def test_no_buttons_no_blocks(self):
        with mock.patch.object(self.sl, "_api") as api:
            self.sl.send("hi")
        self.assertNotIn("blocks", api.call_args.kwargs)

    def test_a_failure_never_raises(self):
        with mock.patch.object(self.sl, "_api", side_effect=OSError("down")):
            self.sl.send("hi")

    def test_api_error_carries_the_code_not_the_token(self):
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, *a): return b'{"ok": false, "error": "invalid_auth"}'
        with mock.patch("urllib.request.urlopen", return_value=R()):
            with self.assertRaises(RuntimeError) as e:
                self.sl._api("chat.postMessage", "xoxb-secret", text="x")
        self.assertIn("invalid_auth", str(e.exception))
        self.assertNotIn("xoxb-secret", str(e.exception))


class Buttons(Base):
    def test_the_owner_in_the_channel_queues_an_approval(self):
        with mock.patch.object(self.sl, "send") as send:
            self.sl.action(click(ME, CHAN, "run|o/r|7"))
        self.assertEqual(self.approvals(), [("o/r", 7, "run")])
        send.assert_called_once()

    def test_answers_to_open_questions_are_queued_too(self):
        with mock.patch.object(self.sl, "send"):
            self.sl.action(click(ME, CHAN, "q:q1:b|o/r|7"))
        self.assertEqual(self.approvals(), [("o/r", 7, "q:q1:b")])

    def test_someone_else_is_not_obeyed_and_only_their_id_and_name_are_kept(self):
        with mock.patch.object(self.sl, "send") as send:
            self.sl.action(click("U0EVIL", CHAN, "run|o/r|7"))
        self.assertEqual(self.approvals(), [])
        send.assert_not_called()
        self.assertEqual([(k["id"], k["name"]) for k in self.unknown()], [("U0EVIL", "someone")])
        self.assertNotIn("run|o/r", json.dumps(self.unknown()))

    def test_the_owner_in_another_channel_is_not_obeyed(self):
        with mock.patch.object(self.sl, "send"):
            self.sl.action(click(ME, "C0OTHER", "run|o/r|7"))
        self.assertEqual(self.approvals(), [])

    def test_a_repo_that_is_not_configured_and_junk_values_are_ignored(self):
        with mock.patch.object(self.sl, "send") as send:
            for value in ("run|evil/repo|7", "rm -rf|o/r|7", "run|o/r|x", "", "run"):
                self.sl.action(click(ME, CHAN, value))
        self.assertEqual(self.approvals(), [])
        send.assert_not_called()

    def test_a_link_button_click_has_no_value_and_does_nothing(self):
        p = click(ME, CHAN, "")
        p["actions"] = [{"action_id": "b1_0"}]
        self.sl.action(p)
        self.assertEqual(self.approvals(), [])


class Commands(Base):
    def cmd(self, text, user=ME, channel="C0ANY"):
        return self.sl.command({"command": "/factory", "text": text, "user_id": user, "channel_id": channel, "user_name": "n"})

    def test_pause_and_resume(self):
        self.assertEqual(self.cmd("pause")["text"], "paused")
        self.assertTrue((Path(self.d) / "PAUSED").exists())
        (Path(self.d) / "pause_until").write_text("9999999999")
        self.assertEqual(self.cmd("resume")["text"], "resumed")
        self.assertFalse((Path(self.d) / "PAUSED").exists())
        self.assertFalse((Path(self.d) / "pause_until").exists())

    def test_status_usage_and_help_reply_only_to_the_asker(self):
        for word in ("status", "usage", "help", "", "nonsense pause"):
            r = self.cmd(word)
            self.assertEqual(r["response_type"], "ephemeral")
            self.assertTrue(r["text"])
        self.assertFalse((Path(self.d) / "PAUSED").exists())      # "nonsense pause" is not a pause

    def test_someone_else_cannot_pause(self):
        r = self.cmd("pause", user="U0EVIL")
        self.assertFalse((Path(self.d) / "PAUSED").exists())
        self.assertIn("not allowed", r["text"])
        self.assertEqual([k["id"] for k in self.unknown()], ["U0EVIL"])


class NotLinkedYet(Base):
    """Connected with only the tokens saved: the person setting it up types /factory and picks themselves in the UI."""
    def setUp(self):
        super().setUp()
        self.sl = S.Slack("xoxb-t", "xapp-t", None, None, self.d, self.db, ["o/r"])

    def test_nobody_is_obeyed_and_the_reply_says_how_to_finish(self):
        r = self.sl.command({"text": "pause", "user_id": ME, "channel_id": CHAN, "user_name": "me"})
        self.assertEqual(r["text"], S.NOT_LINKED)
        self.assertFalse((Path(self.d) / "PAUSED").exists())
        self.assertEqual([(k["id"], k["name"], k["channel"]) for k in self.unknown()], [(ME, "me", CHAN)])
        with mock.patch.object(self.sl, "send"):
            self.sl.action(click(ME, CHAN, "run|o/r|7"))
        self.assertEqual(self.approvals(), [])

    def test_nothing_is_sent_without_a_channel(self):
        with mock.patch.object(self.sl, "_api") as api:
            self.sl.send("hello")
        api.assert_not_called()


class Connection(Base):
    def status(self):
        c = dbm.connect(self.db)
        try:
            return json.loads(dbm.get_status(c)["slack_connection"]["value"])
        finally:
            c.close()

    def test_the_connection_state_is_kept_for_the_ui_without_the_token(self):
        calls = iter([RuntimeError("slack apps.connections.open: invalid_auth"), KeyboardInterrupt()])

        def api(*a, **k):
            raise next(calls)
        with mock.patch.object(self.sl, "_api", api), mock.patch.object(S.time, "sleep"):
            with self.assertRaises(KeyboardInterrupt):
                self.sl.run_forever()
        self.assertEqual(self.status(), {"ok": False, "error": "slack apps.connections.open: invalid_auth"})
        with mock.patch.object(self.sl, "_api", return_value={"url": "wss://x"}), mock.patch.object(S.wsclient, "connect") as conn, \
                mock.patch.object(self.sl, "serve", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.sl.run_forever()
        conn.return_value.close.assert_called_once()
        self.assertEqual(self.status(), {"ok": True, "error": ""})


class Envelopes(Base):
    def test_a_button_is_acknowledged_before_it_is_acted_on(self):
        ws = FakeWS()
        order = []
        ws.send = lambda text: order.append("ack")
        with mock.patch.object(self.sl, "action", side_effect=lambda p: order.append("act")):
            self.sl.handle({"envelope_id": "e1", "type": "interactive", "payload": click(ME, CHAN, "run|o/r|1")}, ws)
        self.assertEqual(order, ["ack", "act"])

    def test_a_slash_command_replies_in_the_ack(self):
        ws = FakeWS()
        self.sl.handle({"envelope_id": "e2", "type": "slash_commands",
                        "payload": {"text": "help", "user_id": ME, "channel_id": CHAN}}, ws)
        self.assertEqual(ws.sent[0]["envelope_id"], "e2")
        self.assertEqual(ws.sent[0]["payload"]["text"], S.HELP)

    def test_hello_and_unknown_types_are_ignored_and_a_failing_action_is_survived(self):
        ws = FakeWS()
        self.sl.handle({"type": "hello"}, ws)
        self.sl.handle({"envelope_id": "e3", "type": "events_api", "payload": {"type": "event_callback"}}, ws)
        with mock.patch.object(self.sl, "action", side_effect=RuntimeError("boom")):
            self.sl.handle({"envelope_id": "e4", "type": "interactive", "payload": {"type": "block_actions"}}, ws)
        self.assertEqual([a["envelope_id"] for a in ws.sent], ["e3", "e4"])
        self.assertEqual(self.approvals(), [])

    def test_serve_handles_envelopes_until_the_server_says_disconnect(self):
        ws = FakeWS([json.dumps({"type": "hello"}),
                     json.dumps({"envelope_id": "e5", "type": "interactive", "payload": click(ME, CHAN, "skip|o/r|3")}),
                     json.dumps({"type": "disconnect", "reason": "refresh_requested"}),
                     json.dumps({"envelope_id": "never", "type": "interactive", "payload": click(ME, CHAN, "run|o/r|4")})])
        with mock.patch.object(self.sl, "send"):
            self.sl.serve(ws)
        self.assertEqual(self.approvals(), [("o/r", 3, "skip")])
        self.assertEqual([a["envelope_id"] for a in ws.sent], ["e5"])


class Config(unittest.TestCase):
    def test_slack_ids_are_checked(self):
        self.assertEqual(_slack_id("U0123ABCDEF", "UW", "user_id"), "U0123ABCDEF")
        self.assertEqual(_slack_id("D0123ABCDEF", "CGD", "channel"), "D0123ABCDEF")
        self.assertIsNone(_slack_id("", "UW", "user_id"))
        self.assertIsNone(_slack_id(None, "CGD", "channel"))
        for bad in ("alice", "u0123abcdef", "C0123ABCDEF", "U1", "U0123 ABCDEF", 5):
            with self.assertRaises(ValueError):
                _slack_id(bad, "UW", "user_id")


class Alerts(unittest.TestCase):
    def fakes(self):
        tg, sl = mock.Mock(), mock.Mock()
        return tg, sl, mock.patch.object(m, "tg", tg), mock.patch.object(m, "sl", sl)

    def test_each_channel_gets_the_alert_its_own_verbosity_allows(self):
        tg, sl, p1, p2 = self.fakes()
        with p1, p2, mock.patch.object(m, "alert_filter", lambda e: True), mock.patch.object(m, "slack_filter", lambda e: e == "failure"), \
                mock.patch.object(m, "emit") as emit:
            m.alert("a", event="info")
            m.alert("b", [("x", "run|o/r|1")], event="failure")
        self.assertEqual([c.args[0] for c in tg.send.call_args_list], ["a", "b"])
        self.assertEqual([c.args[0] for c in sl.send.call_args_list], ["b"])
        self.assertNotIn("not sent", emit.call_args_list[0].args[1])

    def test_one_channel_failing_does_not_stop_the_other_alert_being_recorded(self):
        tg, sl, p1, p2 = self.fakes()
        with p1, p2, mock.patch.object(m, "alert_filter", lambda e: False), mock.patch.object(m, "slack_filter", lambda e: True), \
                mock.patch.object(m, "emit") as emit:
            m.alert("a", event="info")
        tg.send.assert_not_called()
        sl.send.assert_called_once()
        self.assertNotIn("not sent", emit.call_args.args[1])

    def test_with_no_channel_the_alert_is_only_recorded(self):
        with mock.patch.object(m, "tg", None), mock.patch.object(m, "sl", None), mock.patch.object(m, "emit") as emit:
            m.alert("a", event="info")
        self.assertIn("[not sent]", emit.call_args.args[1])


class HealthSenders(unittest.TestCase):
    def test_a_slack_sender_needs_both_tokens_a_channel_and_a_user(self):
        with tempfile.TemporaryDirectory() as d:
            bot, app = Path(d, "bot"), Path(d, "app")
            bot.write_text("xoxb-1\n")
            app.write_text("xapp-1\n")
            cfg = mock.Mock(slack_bot_token_file=str(bot), slack_app_token_file=str(app), slack_channel=CHAN, slack_user_id=ME,
                            db_path=f"{d}/f.db", repos=["o/r"])
            self.assertEqual(health.slack_sender(cfg).__name__, "send")
            for field, value in (("slack_channel", None), ("slack_user_id", None), ("slack_app_token_file", f"{d}/missing")):
                with self.subTest(field):
                    setattr(cfg, field, value)
                    self.assertIsNone(health.slack_sender(cfg))
                    setattr(cfg, field, {"slack_channel": CHAN, "slack_user_id": ME, "slack_app_token_file": str(app)}[field])


if __name__ == "__main__":
    unittest.main()
