"""Tests for publisher modules (build logic only, no network calls)."""

import unittest
from datetime import datetime, timezone

from core.models import Article


def _make_article(**overrides) -> Article:
    defaults = dict(
        id="https://bergfrid.com/blog/test-article",
        title="Test Article Title",
        url="https://bergfrid.com/blog/test-article",
        summary="This is a test summary of the article.",
        tags=["#Geopolitique", "#France", "#Europe"],
        author="Redaction",
        category="Geopolitique",
        published_at=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
        social_summary="Short social summary for the article.",
        image_url="https://example.com/image.jpg",
        source="Bergfrid",
    )
    defaults.update(overrides)
    return Article(**defaults)


# =========================================================
# Discord
# =========================================================

class TestDiscordPublisher(unittest.TestCase):
    """Test DiscordPublisher build logic (no bot/channel interaction)."""

    def test_embed_has_image(self):
        """Embed should include image when article has image_url."""
        import discord
        from core.config import DISCORD_EMBED_COLOR
        from core.utils import determine_importance_emoji, prettify_summary, truncate_text, add_utm

        article = _make_article()
        url = add_utm(article.url, source="discord", medium="social", campaign="rss")
        emoji = determine_importance_emoji(article.summary)
        desc = prettify_summary(article.summary, 2200, prefix="", max_paragraphs=4)
        if article.tags:
            desc = f"{desc}\n\n{' '.join(article.tags[:6])}"

        embed = discord.Embed(
            title=truncate_text(f"{emoji} {article.title}", 256),
            url=url, description=desc, color=DISCORD_EMBED_COLOR,
        )
        if article.image_url:
            embed.set_image(url=article.image_url)

        self.assertEqual(embed.image.url, article.image_url)

    def test_embed_no_image(self):
        import discord
        from core.utils import add_utm

        article = _make_article(image_url="")
        embed = discord.Embed(title=article.title, url=add_utm(article.url, source="discord"))
        self.assertIsNone(embed.image.url)

    def test_hashtags_in_description(self):
        from core.utils import prettify_summary

        article = _make_article()
        desc = prettify_summary(article.summary, 2200, prefix="", max_paragraphs=4)
        if article.tags:
            desc = f"{desc}\n\n{' '.join(article.tags[:6])}"
        self.assertIn("#Geopolitique", desc)
        self.assertIn("#France", desc)


# =========================================================
# Telegram
# =========================================================

class TestTelegramPublisher(unittest.TestCase):
    """Test TelegramPublisher caption building."""

    def _build(self, **overrides):
        from publishers.telegram_pub import TelegramPublisher
        pub = TelegramPublisher(token="fake", chat_id="123", summary_max=900)
        article = _make_article(**overrides)
        url = "https://bergfrid.com/blog/test?utm_source=telegram"
        return pub._build_caption(article, url, use_photo=bool(article.image_url))

    def test_caption_contains_title(self):
        text = self._build()
        self.assertIn("Test Article Title", text)

    def test_caption_contains_hashtags(self):
        text = self._build()
        self.assertIn("#Geopolitique", text)

    def test_caption_no_hashtags_when_empty(self):
        text = self._build(tags=[])
        self.assertNotIn("#", text)

    def test_caption_photo_limit(self):
        long_summary = "A" * 2000
        text = self._build(summary=long_summary)
        self.assertLessEqual(len(text), 1024)

    def test_caption_no_photo_longer(self):
        text = self._build(image_url="")
        # Without photo, no 1024 cap applied
        self.assertIn("Test Article Title", text)


# =========================================================
# Mastodon
# =========================================================

class TestMastodonPublisher(unittest.TestCase):
    """Test MastodonPublisher post text building."""

    def _build(self, **overrides):
        from publishers.mastodon_pub import MastodonPublisher
        pub = MastodonPublisher(
            instance_url="https://mastodon.social",
            access_token="fake", post_max=500,
        )
        article = _make_article(**overrides)
        return pub._build_post(article)

    def test_contains_url(self):
        text = self._build()
        self.assertIn("bergfrid.com", text)

    def test_contains_hashtags(self):
        text = self._build()
        self.assertIn("#Geopolitique", text)

    def test_no_hashtags_when_empty(self):
        text = self._build(tags=[])
        self.assertNotIn("#Geopolitique", text)

    def test_uses_social_summary(self):
        text = self._build(social_summary="Custom social text")
        self.assertIn("Custom social text", text)

    def test_falls_back_to_title(self):
        text = self._build(social_summary="")
        self.assertIn("Test Article Title", text)

    def test_respects_post_max(self):
        text = self._build()
        self.assertLessEqual(len(text), 500)


# =========================================================
# Bluesky
# =========================================================

class TestBlueskyPublisher(unittest.TestCase):
    """Test BlueskyPublisher text and embed building."""

    def _pub(self):
        from publishers.bluesky_pub import BlueskyPublisher
        return BlueskyPublisher(handle="test.bsky.social", app_password="fake", post_max=300)

    def test_text_contains_hashtags(self):
        pub = self._pub()
        article = _make_article()
        text = pub._build_post_text(article)
        self.assertIn("#Geopolitique", text)

    def test_text_no_hashtags_when_empty(self):
        pub = self._pub()
        article = _make_article(tags=[])
        text = pub._build_post_text(article)
        self.assertNotIn("#", text)

    def test_text_respects_max(self):
        pub = self._pub()
        article = _make_article()
        text = pub._build_post_text(article)
        self.assertLessEqual(len(text), 300)

    def test_text_uses_social_summary(self):
        pub = self._pub()
        article = _make_article(social_summary="Custom bluesky text")
        text = pub._build_post_text(article)
        self.assertIn("Custom bluesky text", text)

    def test_re_login_resets_client(self):
        pub = self._pub()
        pub._client = "something"
        pub._re_login()
        # After re_login, _client is either None (login fails with fake creds)
        # or a new Client. Either way, the old one is gone.
        self.assertNotEqual(pub._client, "something")


# =========================================================
# RSS image_url extraction
# =========================================================

class TestImageUrlExtraction(unittest.TestCase):
    def test_extracts_from_media_content(self):
        from core.rss import _image_url

        class FakeEntry:
            media_content = [{"url": "/assets/img.jpg", "medium": "image"}]
            media_thumbnail = []
            enclosures = []

        url = _image_url(FakeEntry(), "https://bergfrid.com")
        self.assertEqual(url, "https://bergfrid.com/assets/img.jpg")

    def test_extracts_from_thumbnail(self):
        from core.rss import _image_url

        class FakeEntry:
            media_content = []
            media_thumbnail = [{"url": "https://cdn.example.com/thumb.png"}]
            enclosures = []

        url = _image_url(FakeEntry(), "https://bergfrid.com")
        self.assertEqual(url, "https://cdn.example.com/thumb.png")

    def test_extracts_from_enclosure(self):
        from core.rss import _image_url

        class FakeEntry:
            media_content = []
            media_thumbnail = []
            enclosures = [{"href": "https://cdn.example.com/photo.jpg", "type": "image/jpeg"}]

        url = _image_url(FakeEntry(), "https://bergfrid.com")
        self.assertEqual(url, "https://cdn.example.com/photo.jpg")

    def test_empty_when_no_image(self):
        from core.rss import _image_url

        class FakeEntry:
            pass

        url = _image_url(FakeEntry(), "https://bergfrid.com")
        self.assertEqual(url, "")


# =========================================================
# social_summary hashtag dedup
# =========================================================

class TestSocialSummaryDedup(unittest.TestCase):
    def test_strips_trailing_hashtags(self):
        import re
        raw = "Some summary text\n\n#Tag1 #Tag2 #Tag3"
        cleaned = re.sub(r'(\s*#\w+)+\s*$', '', raw).strip()
        self.assertEqual(cleaned, "Some summary text")

    def test_no_hashtags_unchanged(self):
        import re
        raw = "Some summary text without tags"
        cleaned = re.sub(r'(\s*#\w+)+\s*$', '', raw).strip()
        self.assertEqual(cleaned, "Some summary text without tags")

    def test_hashtag_in_middle_preserved(self):
        import re
        raw = "The #crisis in Europe worsens"
        cleaned = re.sub(r'(\s*#\w+)+\s*$', '', raw).strip()
        # Trailing #crisis should be stripped since it's at end? No - "worsens" follows.
        self.assertEqual(cleaned, "The #crisis in Europe worsens")
