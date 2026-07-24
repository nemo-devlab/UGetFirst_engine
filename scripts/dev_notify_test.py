#!/usr/bin/env python3
"""Run an instant DEV notification test through the real engine pipeline.

The script skips Apify by creating one synthetic post containing a real DEV
subscriber keyword. It still uses production matching, deduplication, tier
eligibility, provider sending, and database sendout logging.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db  # noqa: E402
import main as engine  # noqa: E402
import notifier  # noqa: E402
from scraper import Post, group_id  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ugetfirst.dev_notify_test")


def _live_eligible_channels(sub: db.Subscriber) -> set[str]:
    channels: set[str] = set()
    if sub.sms_ok and sub.phone and notifier.is_live_destination("sms", sub.phone):
        channels.add("sms")
    if sub.email_ok and sub.email and notifier.is_live_destination("email", sub.email):
        channels.add("email")
    return channels


def _required_channels(requested: str, eligible: set[str]) -> set[str]:
    if requested == "eligible":
        if not eligible:
            raise SystemExit(
                "No live-eligible channel found. Ensure the DEV subscriber uses "
                "QA_TEST_EMAIL/QA_TEST_PHONE and has notification consent enabled."
            )
        return eligible
    required = {"sms", "email"} if requested == "both" else {requested}
    missing = required - eligible
    if missing:
        raise SystemExit(
            "Selected subscriber is not live-eligible for: "
            f"{', '.join(sorted(missing))}. Check tier, consent, and QA allowlist."
        )
    return required


def _validate_provider_config(channels: set[str]) -> None:
    if "sms" in channels and (
        config.SMS_MODE != "twilio"
        or not config.TWILIO_ACCOUNT_SID
        or not config.TWILIO_AUTH_TOKEN
        or not config.TWILIO_FROM_NUMBER
    ):
        raise SystemExit(
            "SMS test requires SMS_MODE=twilio and all TWILIO_* credentials."
        )
    if "email" in channels and not config.RESEND_API_KEY:
        raise SystemExit("Email test requires RESEND_API_KEY on the DEV engine.")


def _find_target(
    subscriber_id: str | None,
) -> tuple[str, str, db.Subscriber, set[str]]:
    monitoring = db.load_monitoring()
    for group_url in sorted(monitoring):
        gid = group_id(group_url)
        if not gid:
            continue
        for sub in sorted(monitoring[group_url], key=lambda item: item.id):
            if subscriber_id and sub.id != subscriber_id:
                continue
            eligible = _live_eligible_channels(sub)
            if eligible:
                return group_url, gid, sub, eligible
    detail = f" with id {subscriber_id}" if subscriber_id else ""
    raise SystemExit(
        f"No alert-ready allowlisted DEV subscriber{detail} was found in a "
        "scrape-enabled monitored group."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subscriber-id",
        help="Optional DEV subscriber UUID. Defaults to the first allowlisted subscriber.",
    )
    parser.add_argument(
        "--channel",
        choices=("eligible", "sms", "email", "both"),
        default="eligible",
        help="Channels to exercise while preserving real tier/consent rules.",
    )
    args = parser.parse_args()

    if config.ENV != "dev":
        raise SystemExit("Refusing to run: dev_notify_test.py requires ENV=dev.")

    group_url, gid, sub, eligible = _find_target(args.subscriber_id)
    channels = _required_channels(args.channel, eligible)
    _validate_provider_config(channels)

    keyword = sub.keywords[0]
    now = datetime.now(timezone.utc)
    post = Post(
        url=f"https://dev.ugetfirst.com/test-notification/{uuid4()}",
        text=f"DEV notification pipeline test containing keyword: {keyword}",
        group_id=gid,
        raw={
            "synthetic": True,
            "source": "scripts/dev_notify_test.py",
            "groupUrl": group_url,
        },
        posted_at=now.isoformat(),
    )

    run_id = db.start_engine_run(1)
    try:
        db.upsert_scraped_posts(
            [post],
            engine_run_id=run_id,
            apify_run_id="dev-synthetic",
        )
        stats = engine.dispatch_posts(
            [post],
            {gid: [sub]},
            channels=channels,
        )
    except Exception as exc:
        db.finish_engine_run(
            run_id,
            posts_scraped=1,
            apify_run_id="dev-synthetic",
            error=str(exc),
        )
        raise
    else:
        db.finish_engine_run(
            run_id,
            posts_scraped=1,
            matches_found=stats.matches_found,
            sms_dispatched=stats.alerts_dispatched,
            apify_run_id="dev-synthetic",
        )

    if stats.matches_found != 1 or stats.alerts_dispatched != len(channels):
        raise SystemExit(
            "DEV notification test did not fully dispatch: "
            f"matches={stats.matches_found}, dispatched={stats.alerts_dispatched}, "
            f"expected={len(channels)}. Check provider logs and DEV sendout rows."
        )

    log.info(
        "DEV notification test passed for tier=%s via %s",
        sub.effective_tier,
        ", ".join(sorted(channels)),
    )


if __name__ == "__main__":
    main()
