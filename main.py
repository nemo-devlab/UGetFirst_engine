"""UGetFirst engine: poll Facebook groups, match keywords, send SMS.

Runs one cycle synchronously, then sleeps so cycles never overlap and never
start more often than MIN_INTERVAL_SECONDS. Idempotency is guaranteed by the
unique (subscriber_id, post_url) constraint on notification_logs.
"""
from __future__ import annotations

import argparse
import logging
import time

import config
import db
import matcher
import notifier
from scraper import group_id, scrape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ugetfirst")


def run_cycle(dump_raw_keys: bool = False) -> None:
    by_group = db.load_monitoring()
    if not by_group:
        log.info("No monitored groups with keywords; nothing to do.")
        return

    group_urls = list(by_group.keys())
    # Index subscribers by numeric group id for robust post -> group matching.
    subs_by_gid: dict[str, list[db.Subscriber]] = {}
    for url, subs in by_group.items():
        gid = group_id(url)
        if gid:
            subs_by_gid.setdefault(gid, []).extend(subs)

    posts = scrape(group_urls, dump_raw_keys=dump_raw_keys)

    sent = 0
    for post in posts:
        subs = subs_by_gid.get(post.group_id or "", [])
        for sub in subs:
            keyword = matcher.first_match(post.text, sub.keywords)
            if not keyword:
                continue
            # Idempotency guard: only proceed if this row is newly inserted.
            if not db.log_notification(sub.id, post.url, keyword):
                continue
            if sub.notify_sms and sub.phone:
                notifier.send(sub.phone, keyword, post.url)
                sent += 1
            else:
                log.info("Logged match for %s but SMS suppressed (opt-out/no phone)", sub.id)
    log.info("Cycle complete: %d post(s), %d SMS dispatched.", len(posts), sent)


def main() -> None:
    parser = argparse.ArgumentParser(description="UGetFirst scraping/notification engine")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit.")
    parser.add_argument(
        "--dump-raw-keys",
        action="store_true",
        help="Log the keys of raw Apify items (to confirm output field names).",
    )
    args = parser.parse_args()

    log.info("Starting engine (ENV=%s, schema=%s, sms=SIMULATED->outbox/)",
             config.ENV, config.DB_SCHEMA)

    if args.once:
        run_cycle(dump_raw_keys=args.dump_raw_keys)
        return

    while True:
        start = time.monotonic()
        try:
            run_cycle(dump_raw_keys=args.dump_raw_keys)
        except Exception:
            log.exception("Cycle failed; will retry next interval.")
        elapsed = time.monotonic() - start
        sleep_for = max(0.0, config.MIN_INTERVAL_SECONDS - elapsed)
        log.info("Sleeping %.1fs until next cycle.", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
