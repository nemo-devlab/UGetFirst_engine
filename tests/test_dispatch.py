import unittest
from unittest.mock import patch

import config
import db
import main
import notifier
from scraper import Post


class DispatchPostsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.subscriber = db.Subscriber(
            id="subscriber-1",
            phone="15550001111",
            email="qa@example.com",
            notify_sms=True,
            notify_email=True,
            sms_consent_at="2026-01-01T00:00:00+00:00",
            plan_tier="speed",
            plan_status="active",
            keywords=["plumber"],
        )
        self.post = Post(
            url="https://dev.ugetfirst.com/test-notification/one",
            text="Looking for a plumber today",
            group_id="123",
            raw={"synthetic": True},
        )

    def test_dispatch_uses_match_dedup_and_both_notifiers(self) -> None:
        with (
            patch.object(config, "SMS_SEND_DELAY_MS", 0),
            patch.object(db, "log_notification", return_value="notification-1"),
            patch.object(db, "log_sendout") as log_sendout,
            patch.object(
                notifier,
                "send",
                return_value=notifier.SendResult(channel="twilio", status="sent"),
            ) as send_sms,
            patch.object(
                notifier,
                "send_email_alert",
                return_value=notifier.SendResult(channel="resend", status="sent"),
            ) as send_email,
        ):
            stats = main.dispatch_posts(
                [self.post],
                {"123": [self.subscriber]},
            )

        self.assertEqual(stats.matches_found, 1)
        self.assertEqual(stats.alerts_dispatched, 2)
        send_sms.assert_called_once()
        send_email.assert_called_once()
        self.assertEqual(log_sendout.call_count, 2)

    def test_duplicate_notification_is_not_sent(self) -> None:
        with (
            patch.object(db, "log_notification", return_value=None),
            patch.object(notifier, "send") as send_sms,
            patch.object(notifier, "send_email_alert") as send_email,
        ):
            stats = main.dispatch_posts(
                [self.post],
                {"123": [self.subscriber]},
            )

        self.assertEqual(stats.matches_found, 0)
        self.assertEqual(stats.alerts_dispatched, 0)
        send_sms.assert_not_called()
        send_email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
