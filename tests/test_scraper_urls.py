import unittest

from scraper import _normalize, _rebuild_post_url


class RebuildPostUrlTests(unittest.TestCase):
    def test_prefers_post_url_over_facebook_group_url(self) -> None:
        item = {
            "facebookUrl": "https://www.facebook.com/groups/6854249724619896",
            "url": "https://www.facebook.com/groups/6854249724619896/permalink/1234567890/",
            "legacyId": "1234567890",
            "text": "Need a plumber",
        }
        self.assertEqual(
            _rebuild_post_url(item),
            "https://www.facebook.com/groups/6854249724619896/permalink/1234567890/",
        )
        post = _normalize(item)
        assert post is not None
        self.assertEqual(
            post.url,
            "https://www.facebook.com/groups/6854249724619896/permalink/1234567890/",
        )
        self.assertEqual(post.group_id, "6854249724619896")

    def test_rebuilds_from_group_and_legacy_id(self) -> None:
        item = {
            "facebookUrl": "https://www.facebook.com/groups/6854249724619896",
            "legacyId": "9876543210",
            "text": "Looking for cleaners",
        }
        self.assertEqual(
            _rebuild_post_url(item),
            "https://www.facebook.com/groups/6854249724619896/posts/9876543210/",
        )

    def test_rebuilds_from_feedback_id(self) -> None:
        item = {
            "facebookUrl": "https://www.facebook.com/groups/111222333",
            "feedbackId": "ZmVlZGJhY2s6MjE2MDQ3OTM0NDM2MTAzNQ==",
            "text": "hi",
        }
        self.assertEqual(
            _rebuild_post_url(item),
            "https://www.facebook.com/groups/111222333/posts/2160479344361035/",
        )

    def test_rebuilds_with_slug_group_path(self) -> None:
        item = {
            "facebookUrl": "https://www.facebook.com/groups/selftaughtprogrammers/",
            "legacyId": "2616394328724285",
            "text": "hi",
        }
        self.assertEqual(
            _rebuild_post_url(item),
            "https://www.facebook.com/groups/selftaughtprogrammers/posts/2616394328724285/",
        )

    def test_skips_group_only_without_post_id(self) -> None:
        item = {
            "facebookUrl": "https://www.facebook.com/groups/6854249724619896",
            "text": "Need help",
        }
        self.assertIsNone(_rebuild_post_url(item))
        self.assertIsNone(_normalize(item))


if __name__ == "__main__":
    unittest.main()
