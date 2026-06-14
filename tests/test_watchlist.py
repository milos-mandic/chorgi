"""Tests for agent/watchlist.py — detection, enrichment parsing, and storage.

No network: HTML/JSON is fed to the parser directly and fetch is monkeypatched.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills"))

from agent import watchlist as wl


class TestDetection(unittest.TestCase):
    def test_is_watchable(self):
        for url in [
            "https://www.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://vimeo.com/12345",
            "https://www.imdb.com/title/tt0111161/",
            "https://m.twitch.tv/somestream",
        ]:
            self.assertTrue(wl.is_watchable(url), url)

    def test_not_watchable(self):
        for url in [
            "https://example.com/article",
            "https://news.ycombinator.com/item?id=1",
            "https://github.com/foo/bar",
        ]:
            self.assertFalse(wl.is_watchable(url), url)

    def test_watch_intent(self):
        self.assertTrue(wl.has_watch_intent("watch this later: http://x.com"))
        self.assertTrue(wl.has_watch_intent("add to my watchlist"))
        self.assertTrue(wl.has_watch_intent("save to watch later"))
        self.assertFalse(wl.has_watch_intent("bookmark this article"))

    def test_source_of(self):
        self.assertEqual(wl.source_of("https://youtu.be/abc"), "youtube")
        self.assertEqual(wl.source_of("https://www.imdb.com/title/tt1/"), "imdb")
        self.assertEqual(wl.source_of("https://vimeo.com/1"), "vimeo")
        self.assertEqual(wl.source_of("https://www.disneyplus.com/x"), "disney+")


class TestDuration(unittest.TestCase):
    def test_format(self):
        self.assertEqual(wl.format_duration("PT1H23M45S"), "1h 23m")
        self.assertEqual(wl.format_duration("PT23M"), "23m")
        self.assertEqual(wl.format_duration("PT45S"), "45s")
        self.assertEqual(wl.format_duration("PT2H"), "2h")
        self.assertEqual(wl.format_duration(""), "")
        self.assertEqual(wl.format_duration("garbage"), "")


class TestParsing(unittest.TestCase):
    def test_og_and_ldjson(self):
        html = """
        <html><head>
          <title>Fallback Title</title>
          <meta property="og:title" content="The Shawshank Redemption">
          <meta property="og:image" content="https://img/poster.jpg">
          <meta property="og:description" content="Two imprisoned men bond.">
          <script type="application/ld+json">
            {"@type":"Movie","name":"Shawshank",
             "aggregateRating":{"ratingValue":"9.3"},
             "duration":"PT2H22M"}
          </script>
        </head><body></body></html>
        """
        p = wl._WatchMetaParser()
        p.feed(html)
        self.assertEqual(p.og_title, "The Shawshank Redemption")
        self.assertEqual(p.og_image, "https://img/poster.jpg")
        rating, duration = wl._ld_rating_and_duration(p.ld_blocks)
        self.assertEqual(rating, "9.3")
        self.assertEqual(duration, "2h 22m")

    def test_ldjson_graph(self):
        block = '{"@graph":[{"@type":"VideoObject","duration":"PT12M"}]}'
        rating, duration = wl._ld_rating_and_duration([block])
        self.assertEqual(duration, "12m")
        self.assertEqual(rating, "")

    def test_ldjson_bad_json_tolerated(self):
        rating, duration = wl._ld_rating_and_duration(["{not json"])
        self.assertEqual((rating, duration), ("", ""))

    def test_imdb_suggestion_enrichment(self):
        suggestion = ('{"d":[{"id":"tt0111161","l":"The Shawshank Redemption",'
                      '"y":1994,"i":{"imageUrl":"https://m.media-amazon.com/p.jpg"}}]}')
        # IMDB path must NOT scrape HTML (it 202s) — only the suggestion API.
        def fake_fetch(url, timeout=6):
            assert "suggestion" in url, url
            return suggestion
        with mock.patch.object(wl, "_fetch", side_effect=fake_fetch):
            meta = wl.fetch_watch_meta("https://www.imdb.com/title/tt0111161/")
        self.assertEqual(meta["title"], "The Shawshank Redemption")
        self.assertEqual(meta["image"], "https://m.media-amazon.com/p.jpg")
        self.assertIn("1994", meta["description"])

    def test_imdb_id_extraction(self):
        self.assertEqual(
            wl._IMDB_ID_RE.search("https://www.imdb.com/title/tt0111161/?ref=x").group(1),
            "tt0111161")
        self.assertIsNone(wl._IMDB_ID_RE.search("https://www.imdb.com/name/nm1/"))

    def test_fetch_watch_meta_oembed_wins(self):
        page = ('<html><head><title>yt</title>'
                '<meta property="og:title" content="OG title">'
                '<meta property="og:image" content="https://og/img.jpg">'
                '</head></html>')
        with mock.patch.object(wl, "_fetch", return_value=page), \
             mock.patch.object(wl, "_oembed", return_value={
                 "title": "Clean oEmbed Title",
                 "thumbnail_url": "https://oembed/thumb.jpg"}):
            meta = wl.fetch_watch_meta("https://youtu.be/abc")
        self.assertEqual(meta["title"], "Clean oEmbed Title")
        self.assertEqual(meta["image"], "https://oembed/thumb.jpg")


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "watchlist.json"
        self._orig = wl.WATCHLIST_FILE
        wl.WATCHLIST_FILE = self.path

    def tearDown(self):
        wl.WATCHLIST_FILE = self._orig
        self.tmp.cleanup()

    def test_add_list_mark_remove(self):
        url = "https://youtu.be/abc"
        n = wl.add_watch_item(url, title="A Video", summary="s",
                              image="i", rating="8.0", duration="12m",
                              source="youtube", tags=["fun"])
        self.assertEqual(n, 1)
        items = wl.load_watch_items()
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["title"], "A Video")
        self.assertEqual(it["source"], "youtube")
        self.assertFalse(it["watched"])

        # Upsert: same url updates, count of unwatched unchanged
        n2 = wl.add_watch_item(url, title="A Video (HD)")
        self.assertEqual(n2, 1)
        self.assertEqual(len(wl.load_watch_items()), 1)
        self.assertEqual(wl.load_watch_items()[0]["title"], "A Video (HD)")

        # Mark watched -> unwatched count drops
        self.assertTrue(wl.set_watched(url, True))
        self.assertTrue(wl.load_watch_items()[0]["watched"])
        self.assertFalse(wl.set_watched("https://nope", True))

        # add_watch_item return reflects unwatched-only count
        n3 = wl.add_watch_item("https://youtu.be/def", title="B")
        self.assertEqual(n3, 1)  # one watched, one unwatched

    def test_source_default_when_blank(self):
        wl.add_watch_item("https://vimeo.com/9", title="V")
        self.assertEqual(wl.load_watch_items()[0]["source"], "vimeo")

    def test_where_url(self):
        imdb = "https://www.imdb.com/title/tt0111161/"
        wl.add_watch_item(imdb, title="Shawshank", source="imdb")
        it = wl.load_watch_items()[0]
        self.assertEqual(it["where_url"], "")
        # Attach a streaming link later.
        self.assertTrue(wl.set_where(imdb, "https://www.netflix.com/title/70005379"))
        it = wl.load_watch_items()[0]
        self.assertEqual(it["where_source"], "netflix")
        self.assertTrue(it["where_url"].startswith("https://www.netflix.com"))
        # Clearing it resets both fields.
        self.assertTrue(wl.set_where(imdb, ""))
        self.assertEqual(wl.load_watch_items()[0]["where_source"], "")
        self.assertFalse(wl.set_where("https://nope", "x"))

    def test_add_with_where(self):
        wl.add_watch_item("https://youtu.be/x", title="V",
                          where_url="https://www.disneyplus.com/movie/y")
        it = wl.load_watch_items()[0]
        self.assertEqual(it["where_source"], "disney+")


if __name__ == "__main__":
    unittest.main()
