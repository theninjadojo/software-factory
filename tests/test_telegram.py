import unittest

from factory.telegram import authorized, parse_callback

ME = 111


def msg(chat, sender, text="/status"):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "from": {"id": sender}, "text": text}}


def cb(chat, sender, data):
    return {"update_id": 2, "callback_query": {"id": "x", "from": {"id": sender}, "data": data,
                                               "message": {"chat": {"id": chat}}}}


class T(unittest.TestCase):
    def test_only_owner_authorized(self):
        self.assertTrue(authorized(msg(ME, ME), ME))
        self.assertFalse(authorized(msg(222, 222), ME))
        self.assertFalse(authorized(msg(-100, ME), ME))      # group chat
        self.assertFalse(authorized(msg(ME, 222), ME))       # someone else in my chat
        self.assertFalse(authorized({"update_id": 3}, ME))
        self.assertTrue(authorized(cb(ME, ME, "run|a/b|1"), ME))
        self.assertFalse(authorized(cb(ME, 222, "run|a/b|1"), ME))

    def test_callback_parsing(self):
        self.assertEqual(parse_callback("run|owner/repo|12"), ("run", "owner/repo", 12))
        self.assertEqual(parse_callback("skip|o/r|3"), ("skip", "o/r", 3))
        for bad in ("rm|o/r|1", "run|o/r|x", "run|o|1", "run|o/r/z|1", "run|o/r", "", "run|o/r|1|2"):
            self.assertIsNone(parse_callback(bad), bad)


if __name__ == "__main__":
    unittest.main()
