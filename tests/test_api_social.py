"""Tests for the dashboard's /api/social/* routes (agent/api_handlers.py).

Handlers are called directly against a temp posts.json + images dir.
"""

import base64
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "skills"))
sys.path.insert(0, str(BASE / "skills" / "social"))

from agent import api_handlers  # noqa: E402
import social_cli as sc  # noqa: E402

PNG_URI = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()


class ApiSocialTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig = (sc.DATA_FILE, sc.IMAGES_DIR)
        sc.DATA_FILE = tmp / "posts.json"
        sc.IMAGES_DIR = tmp / "images"
        api_handlers._social_cli = sc  # skip the lazy import

    def tearDown(self):
        api_handlers._social_cli = None
        sc.DATA_FILE, sc.IMAGES_DIR = self._orig
        self._tmp.cleanup()

    def write(self, path, method, body=None):
        return api_handlers.api_write(path, method, {} if body is None else body, None)

    def create(self, **fields):
        body = {"platform": "linkedin", "scheduled_at": "2026-09-21 09:00", "text": "Hello"}
        body.update(fields)
        status, payload = self.write("/api/social/posts", "POST", body)
        self.assertEqual(status, 200, payload)
        return payload

    def test_create_then_duplicate_slot_is_409(self):
        first = self.create()
        status, payload = self.write("/api/social/posts", "POST", {
            "platform": "linkedin", "scheduled_at": "2026-09-21 20:00", "text": "Again"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["conflict_id"], first["id"])
        self.assertIn("error", payload)

    def test_bad_json_and_invalid_fields_are_400(self):
        self.assertEqual(api_handlers.api_write("/api/social/posts", "POST", None, None)[0], 400)
        status, payload = self.write("/api/social/posts", "POST", {"platform": "linkedin"})
        self.assertEqual(status, 400)
        self.assertIn("scheduled_at", payload["error"])

    def test_patch_and_delete(self):
        post = self.create()
        status, payload = self.write(f"/api/social/posts/{post['id']}", "PATCH", {"status": "posted"})
        self.assertEqual((status, payload["status"]), (200, "posted"))
        self.assertEqual(self.write("/api/social/posts/sp_missing", "PATCH", {"text": "x"})[0], 404)
        self.assertEqual(self.write(f"/api/social/posts/{post['id']}", "DELETE"), (200, {"deleted": True}))
        self.assertEqual(self.write(f"/api/social/posts/{post['id']}", "DELETE")[0], 404)

    def test_image_routes_are_not_mistaken_for_post_routes(self):
        post = self.create()
        status, payload = self.write(f"/api/social/posts/{post['id']}/image", "POST",
                                     {"data_uri": PNG_URI, "alt": "pic"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["image"]["alt"], "pic")
        status, payload = self.write(f"/api/social/posts/{post['id']}/image", "DELETE")
        self.assertEqual((status, payload["image"]), (200, None))
        self.assertIsNotNone(sc.find_post(sc.load_posts(), post["id"]))

    def test_move_swaps(self):
        a = self.create()
        b = self.create(platform="substack_note", scheduled_at="2026-09-22 12:00")
        status, payload = self.write("/api/social/move", "POST",
                                     {"id": a["id"], "platform": "substack_note", "date": "2026-09-22"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["swapped"]["id"], b["id"])

    def test_import_preview_apply_and_bad_mode(self):
        doc = {"version": 1, "posts": [
            {"platform": "linkedin", "scheduled_at": "2026-09-23 08:00", "text": "One"}]}
        status, preview = self.write("/api/social/import", "POST", {"mode": "preview", "doc": doc})
        self.assertEqual((status, preview["ok"]), (200, True))
        self.assertEqual(sc.load_posts(), [])
        status, result = self.write("/api/social/import", "POST", {"mode": "apply", "doc": doc})
        self.assertEqual((status, result["imported"]), (200, 1))
        status, _ = self.write("/api/social/import", "POST", {"mode": "apply", "doc": doc})
        self.assertEqual(status, 409)
        self.assertEqual(self.write("/api/social/import", "POST", {"mode": "yolo", "doc": doc})[0], 400)

    def test_get_routes(self):
        self.create()
        status, payload = api_handlers.api_get("/api/social/posts")
        self.assertEqual((status, len(payload["posts"])), (200, 1))
        status, schema = api_handlers.api_get("/api/social/schema")
        self.assertEqual((status, schema["$id"]), (200, "urn:chorgi:social_posts:v1"))

    def test_unknown_social_route_is_404(self):
        self.assertEqual(self.write("/api/social/nope", "POST")[0], 404)


if __name__ == "__main__":
    unittest.main()
