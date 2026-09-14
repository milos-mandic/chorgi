"""Tests for skills/social/social_cli.py — storage, the import contract, nudges.

Every test runs against a temp posts.json + images dir; nothing touches the real
workspace, the network, or Telegram.
"""

import asyncio
import base64
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "skills"))
sys.path.insert(0, str(BASE / "skills" / "social"))

import _shared  # noqa: E402
import social_cli as sc  # noqa: E402
from agent import api_handlers  # noqa: E402
from agent.scheduler import Scheduler  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
EXAMPLE = BASE / "skills" / "social" / "schema" / "example_week.json"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24
# Unicode bold, a ZWJ emoji sequence, leading/trailing spaces, runs of blank lines.
EXACT_TEXT = "𝗕𝗼𝗹𝗱 hook line\n\n  indented, trailing spaces  \n👩‍💻 emoji\n\n\nend "


def data_uri(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


class SocialBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig = (sc.DATA_FILE, sc.IMAGES_DIR)
        sc.DATA_FILE = tmp / "posts.json"
        sc.IMAGES_DIR = tmp / "images"

    def tearDown(self):
        sc.DATA_FILE, sc.IMAGES_DIR = self._orig
        self._tmp.cleanup()

    def make(self, **fields) -> dict:
        body = {"platform": "linkedin", "scheduled_at": "2026-09-21 09:00",
                "text": "Hello", "status": "ready"}
        body.update(fields)
        return sc.create_post(body)

    def stored(self, post_id: str) -> dict | None:
        return sc.find_post(sc.load_posts(), post_id)

    def assertRefused(self, status: int, fn, *args) -> sc.SocialError:
        with self.assertRaises(sc.SocialError) as ctx:
            fn(*args)
        self.assertEqual(ctx.exception.status, status, ctx.exception.message)
        return ctx.exception


class TestParseLocalDatetime(unittest.TestCase):
    def test_naive_is_berlin_summer(self):
        self.assertEqual(_shared.parse_local_datetime("2026-07-30 09:00").isoformat(),
                         "2026-07-30T09:00:00+02:00")

    def test_t_separator_seconds_winter(self):
        self.assertEqual(_shared.parse_local_datetime("2026-01-30T09:00:15").isoformat(),
                         "2026-01-30T09:00:15+01:00")

    def test_offset_is_converted_to_berlin(self):
        self.assertEqual(_shared.parse_local_datetime("2026-07-30T07:00:00Z").isoformat(),
                         "2026-07-30T09:00:00+02:00")

    def test_rejects_non_datetimes(self):
        for bad in ("2026-07-30", "2026-13-01 09:00", "tomorrow 9am", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _shared.parse_local_datetime(bad)


class TestPosts(SocialBase):
    def test_text_round_trips_exactly(self):
        post = self.make(text=EXACT_TEXT)
        self.assertEqual(self.stored(post["id"])["text"], EXACT_TEXT)
        on_disk = json.loads(sc.DATA_FILE.read_text())["posts"][0]["text"]
        self.assertEqual(on_disk, EXACT_TEXT)

    def test_scheduled_at_stored_as_berlin_iso(self):
        self.assertEqual(self.make()["scheduled_at"], "2026-09-21T09:00:00+02:00")

    def test_second_post_same_platform_same_day_conflicts(self):
        first = self.make()
        err = self.assertRefused(409, sc.create_post, {
            "platform": "linkedin", "scheduled_at": "2026-09-21 18:00", "text": "Again"})
        self.assertEqual(err.details["conflict_id"], first["id"])
        self.assertEqual(len(sc.load_posts()), 1)

    def test_other_platform_same_day_is_fine(self):
        self.make()
        self.make(platform="substack_note")
        self.assertEqual(len(sc.load_posts()), 2)

    def test_whitespace_only_text_refused(self):
        self.assertRefused(400, sc.create_post, {
            "platform": "linkedin", "scheduled_at": "2026-09-21 09:00", "text": " \n\t "})

    def test_linkedin_limit_is_3000_but_substack_allows_more(self):
        self.assertRefused(400, sc.create_post, {
            "platform": "linkedin", "scheduled_at": "2026-09-21 09:00", "text": "x" * 3001})
        self.make(platform="substack_note", text="x" * 3001)

    def test_limit_counts_code_points_not_bytes(self):
        self.make(text="𝗕" * 3000)  # 4 UTF-8 bytes / 2 UTF-16 units each

    def test_update_into_taken_slot_conflicts(self):
        self.make()
        other = self.make(scheduled_at="2026-09-22 09:00")
        self.assertRefused(409, sc.update_post, other["id"], {"scheduled_at": "2026-09-21 12:00"})
        self.assertEqual(self.stored(other["id"])["scheduled_at"], "2026-09-22T09:00:00+02:00")

    def test_update_time_within_same_day(self):
        post = self.make()
        sc.update_post(post["id"], {"scheduled_at": "2026-09-21 17:45"})
        self.assertEqual(self.stored(post["id"])["scheduled_at"], "2026-09-21T17:45:00+02:00")

    def test_update_missing_post_is_404(self):
        self.assertRefused(404, sc.update_post, "sp_nope", {"text": "x"})

    def test_reschedule_clears_nudge_but_same_minute_keeps_it(self):
        post = self.make(scheduled_at="2026-09-21 09:00:30")
        sc.mark_nudged(post["id"])
        sc.update_post(post["id"], {"scheduled_at": "2026-09-21 09:00", "text": "edited"})
        kept = self.stored(post["id"])
        self.assertEqual(kept["scheduled_at"], "2026-09-21T09:00:30+02:00")
        self.assertIsNotNone(kept["nudged_at"])
        sc.update_post(post["id"], {"scheduled_at": "2026-09-21 10:00"})
        self.assertIsNone(self.stored(post["id"])["nudged_at"])

    def test_posted_at_follows_status(self):
        post = self.make(status="draft")
        self.assertIsNone(post["posted_at"])
        sc.update_post(post["id"], {"status": "posted"})
        self.assertIsNotNone(self.stored(post["id"])["posted_at"])
        sc.update_post(post["id"], {"status": "ready"})
        self.assertIsNone(self.stored(post["id"])["posted_at"])

    def test_move_to_empty_cell_keeps_time_and_fixes_dst_offset(self):
        post = self.make(scheduled_at="2026-10-23 08:15")  # CEST
        sc.move_post(post["id"], "substack_note", "2026-10-26")  # CET
        moved = self.stored(post["id"])
        self.assertEqual(moved["platform"], "substack_note")
        self.assertEqual(moved["scheduled_at"], "2026-10-26T08:15:00+01:00")

    def test_move_onto_occupied_cell_swaps(self):
        a = self.make(text="A")
        b = self.make(platform="substack_note", scheduled_at="2026-09-23 17:30", text="B")
        result = sc.move_post(a["id"], "substack_note", "2026-09-23")
        self.assertEqual(result["swapped"]["id"], b["id"])
        a2, b2 = self.stored(a["id"]), self.stored(b["id"])
        self.assertEqual((a2["platform"], a2["scheduled_at"]),
                         ("substack_note", "2026-09-23T09:00:00+02:00"))
        self.assertEqual((b2["platform"], b2["scheduled_at"]),
                         ("linkedin", "2026-09-21T17:30:00+02:00"))

    def test_move_refuses_overlong_text_into_linkedin(self):
        long_note = self.make(platform="substack_note", text="x" * 3500)
        self.assertRefused(400, sc.move_post, long_note["id"], "linkedin", "2026-09-24")
        self.assertEqual(self.stored(long_note["id"])["platform"], "substack_note")

    def test_move_bad_date(self):
        post = self.make()
        self.assertRefused(400, sc.move_post, post["id"], "linkedin", "2026-02-30")
        self.assertRefused(400, sc.move_post, post["id"], "linkedin", "Monday")


class TestImages(SocialBase):
    def test_replacing_an_image_deletes_the_old_file(self):
        post = self.make()
        first = sc.image_path(sc.set_image(post["id"], data_uri(PNG), "alt")["image"]["file"])
        self.assertTrue(first.is_file())
        second = sc.set_image(post["id"], data_uri(JPEG, "image/jpeg"))["image"]["file"]
        self.assertTrue(second.endswith(".jpg"))
        self.assertTrue(sc.image_path(second).is_file())
        self.assertFalse(first.exists())

    def test_same_bytes_twice_keeps_the_file(self):
        post = self.make()
        sc.set_image(post["id"], data_uri(PNG))
        name = sc.set_image(post["id"], data_uri(PNG))["image"]["file"]
        self.assertTrue(sc.image_path(name).is_file())

    def test_declared_type_must_match_bytes(self):
        post = self.make()
        self.assertRefused(400, sc.set_image, post["id"], data_uri(JPEG, "image/png"))
        self.assertRefused(400, sc.set_image, post["id"], data_uri(b"not an image at all", "image/png"))

    def test_remove_and_delete_clean_up_files(self):
        post = self.make()
        path = sc.image_path(sc.set_image(post["id"], data_uri(PNG))["image"]["file"])
        sc.remove_image(post["id"])
        self.assertFalse(path.exists())
        path = sc.image_path(sc.set_image(post["id"], data_uri(PNG))["image"]["file"])
        sc.delete_post(post["id"])
        self.assertFalse(path.exists())
        self.assertEqual(sc.load_posts(), [])

    def test_image_path_only_accepts_our_names(self):
        self.assertIsNone(sc.image_path("../posts.json"))
        self.assertIsNone(sc.image_path("sp_1_abc.exe"))
        self.assertIsNone(sc.image_path("sp_1/../x.png"))
        self.assertEqual(sc.image_path("sp_1_abc.png"), sc.IMAGES_DIR / "sp_1_abc.png")


def doc(*posts) -> dict:
    return {"version": 1, "posts": list(posts)}


def post(**fields) -> dict:
    base = {"platform": "linkedin", "scheduled_at": "2026-09-21 09:00", "text": "Hi"}
    base.update(fields)
    return base


class TestValidateDoc(SocialBase):
    def paths(self, document) -> list[str]:
        return [e["path"] for e in sc.validate_doc(document)]

    def test_example_file_is_valid(self):
        self.assertEqual(sc.validate_doc(json.loads(EXAMPLE.read_text())), [])

    def test_validator_rules_come_from_the_schema(self):
        schema_platforms = sc.load_schema()["$defs"]["post"]["properties"]["platform"]["enum"]
        self.assertEqual(sorted(sc.PLATFORM_LABELS), sorted(schema_platforms))
        self.assertEqual(sc.rules()["text_max_by_platform"], {"linkedin": 3000})

    def test_document_level_errors(self):
        self.assertEqual(self.paths([]), ["$"])
        self.assertIn("$.version", self.paths({"posts": [post()]}))
        self.assertIn("$.version", self.paths({"version": 2, "posts": [post()]}))
        self.assertIn("$.version", self.paths({"version": True, "posts": [post()]}))
        self.assertIn("$.posts", self.paths(doc()))
        self.assertIn("$.extra", self.paths({**doc(post()), "extra": 1}))

    def test_post_field_errors(self):
        cases = {
            "$.posts[0].titel": post(titel="typo"),
            "$.posts[0].text": post(text=None),
            "$.posts[0].platform": post(platform="twitter"),
            "$.posts[0].scheduled_at": post(scheduled_at="next monday"),
            "$.posts[0].status": post(status="published"),
            "$.posts[0].notes": post(notes=5),
        }
        for path, item in cases.items():
            with self.subTest(path=path):
                self.assertIn(path, self.paths(doc(item)))
        missing = post()
        del missing["text"]
        self.assertIn("$.posts[0].text", self.paths(doc(missing)))

    def test_impossible_date(self):
        self.assertIn("$.posts[0].scheduled_at", self.paths(doc(post(scheduled_at="2026-02-30 09:00"))))

    def test_whitespace_only_and_linkedin_limit(self):
        self.assertIn("$.posts[0].text", self.paths(doc(post(text="  \n"))))
        self.assertIn("$.posts[0].text", self.paths(doc(post(text="x" * 3001))))
        self.assertEqual(self.paths(doc(post(platform="substack_note", text="x" * 3001))), [])

    def test_duplicate_slot_in_one_file(self):
        errors = sc.validate_doc(doc(post(), post(scheduled_at="2026-09-21 18:00")))
        self.assertEqual([e["path"] for e in errors], ["$.posts[1].scheduled_at"])
        self.assertEqual(errors[0]["index"], 1)

    def test_offset_that_lands_on_the_same_berlin_day_is_a_duplicate(self):
        # 23:30Z on the 20th is 01:30 on the 21st in Berlin.
        self.assertIn("$.posts[1].scheduled_at",
                      self.paths(doc(post(), post(scheduled_at="2026-09-20T23:30:00Z"))))

    def test_image_errors(self):
        cases = [
            post(image="data:image/png;base64,AAAA"),
            post(image={"data_uri": "data:image/png;base64,@@@@"}),
            post(image={"data_uri": data_uri(JPEG, "image/png")}),
            post(image={"data_uri": data_uri(PNG), "caption": "x"}),
            post(image={}),
        ]
        for item in cases:
            with self.subTest(image=str(item["image"])[:40]):
                self.assertTrue(any(p.startswith("$.posts[0].image") for p in self.paths(doc(item))))
        self.assertEqual(self.paths(doc(post(image={"data_uri": data_uri(PNG), "alt": "ok"}))), [])


class TestImport(SocialBase):
    def test_preview_reports_new_and_conflict_without_saving(self):
        self.make()
        document = doc(post(scheduled_at="2026-09-21 10:00"),
                       post(platform="substack_note"))
        preview = sc.preview_import(document)
        self.assertFalse(preview["ok"])
        self.assertEqual([r["action"] for r in preview["rows"]], ["conflict", "new"])
        preview = sc.preview_import(document, overwrite=True)
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["counts"], {"new": 1, "replace": 1, "conflict": 0, "error": 0})
        self.assertEqual(len(sc.load_posts()), 1)

    def test_preview_marks_errors_and_past_times(self):
        document = doc(post(platform="twitter"), post(platform="substack_note"))
        preview = sc.preview_import(document, now=datetime(2026, 9, 22, tzinfo=timezone.utc))
        self.assertEqual([r["action"] for r in preview["rows"]], ["error", "new"])
        self.assertTrue(preview["rows"][0]["errors"])
        self.assertIn("scheduled time is in the past", preview["rows"][1]["warnings"])
        self.assertFalse(preview["ok"])

    def test_apply_refuses_conflicts_without_overwrite(self):
        existing = self.make(text="keep me")
        self.assertRefused(409, sc.apply_import, doc(post(), post(platform="substack_note")))
        self.assertEqual([p["id"] for p in sc.load_posts()], [existing["id"]])

    def test_apply_with_overwrite_replaces_and_removes_old_image(self):
        existing = self.make()
        old_image = sc.image_path(sc.set_image(existing["id"], data_uri(PNG))["image"]["file"])
        result = sc.apply_import(doc(post(text=EXACT_TEXT), post(platform="substack_note")), overwrite=True)
        self.assertEqual((result["imported"], result["replaced"]), (2, 1))
        self.assertEqual(result["first_date"], "2026-09-21")
        posts = sc.load_posts()
        self.assertNotIn(existing["id"], [p["id"] for p in posts])
        self.assertFalse(old_image.exists())
        imported = next(p for p in posts if p["platform"] == "linkedin")
        self.assertEqual(imported["text"], EXACT_TEXT)
        self.assertEqual(imported["status"], "ready")  # the contract's default

    def test_apply_writes_images(self):
        result = sc.apply_import(doc(post(image={"data_uri": data_uri(PNG), "alt": "a chart"})))
        image = result["posts"][0]["image"]
        self.assertEqual(image["alt"], "a chart")
        self.assertEqual(sc.image_path(image["file"]).read_bytes(), PNG)

    def test_apply_with_errors_saves_nothing(self):
        err = self.assertRefused(400, sc.apply_import, doc(post(), post(platform="twitter")))
        self.assertEqual(err.details["errors"][0]["path"], "$.posts[1].platform")
        self.assertEqual(sc.load_posts(), [])

    def test_example_file_imports(self):
        result = sc.apply_import(json.loads(EXAMPLE.read_text()))
        self.assertEqual(result["imported"], 5)

    def test_cli_validate_and_dry_run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sc.main(["validate", str(EXAMPLE)]), 0)
            self.assertEqual(sc.main(["import", str(EXAMPLE), "--dry-run"]), 0)
        self.assertIn("valid", out.getvalue())
        self.assertEqual(sc.load_posts(), [])


class FakeOrchestrator:
    def __init__(self):
        self.sent: list[str] = []
        self.photos: list[Path] = []

    async def send_raw_to_user(self, text):
        self.sent.append(text)

    async def send_photo_to_user(self, path, caption=None):
        self.photos.append(Path(path))


class TestNudges(SocialBase):
    def test_due_window(self):
        post_ = self.make(scheduled_at="2026-09-21 09:00")  # 07:00 UTC
        at = lambda h, m=0: datetime(2026, 9, 21, h, m, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(sc.due_for_nudge(at(6, 55)), [])
        self.assertEqual([p["id"] for p in sc.due_for_nudge(at(7, 5))], [post_["id"]])
        self.assertEqual(sc.due_for_nudge(at(7) + timedelta(hours=13)), [])
        sc.mark_nudged(post_["id"])
        self.assertEqual(sc.due_for_nudge(at(7, 5)), [])

    def test_posted_posts_are_not_nudged(self):
        self.make(scheduled_at="2026-09-21 09:00", status="posted")
        self.assertEqual(sc.due_for_nudge(datetime(2026, 9, 21, 7, 5, tzinfo=timezone.utc)), [])

    def test_heartbeat_sends_header_exact_text_and_image_once(self):
        when = datetime.now(BERLIN) - timedelta(minutes=2)
        post_ = self.make(scheduled_at=when.strftime("%Y-%m-%d %H:%M"), text=EXACT_TEXT, status="draft")
        sc.set_image(post_["id"], data_uri(PNG))
        api_handlers._social_cli = sc
        try:
            orch = FakeOrchestrator()
            scheduler = Scheduler(orch)
            asyncio.run(scheduler._nudge_social_posts())
            asyncio.run(scheduler._nudge_social_posts())
        finally:
            api_handlers._social_cli = None
        self.assertEqual(len(orch.sent), 2)
        self.assertIn("LinkedIn", orch.sent[0])
        self.assertIn("draft", orch.sent[0])
        self.assertEqual(orch.sent[1], EXACT_TEXT)
        self.assertEqual(len(orch.photos), 1)
        self.assertIsNotNone(self.stored(post_["id"])["nudged_at"])


if __name__ == "__main__":
    unittest.main()
