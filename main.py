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
import health
import matcher
import notifier
from scraper import group_id, scrape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ugetfirst")


def run_cycle(dump_raw_keys: bool = False) -> None:
    run_id: str | None = None
    posts_scraped = 0
    matches_found = 0
    sent = 0
    apify_run_id: str | None = None

    try:
        by_group = db.load_monitoring()
        if not by_group:
            log.info("No monitored groups with keywords; nothing to do.")
            run_id = db.start_engine_run(0)
            db.finish_engine_run(run_id)
            return

        group_urls = list(by_group.keys())
        run_id = db.start_engine_run(len(group_urls))

        # Index subscribers by numeric group id for robust post -> group matching.
        subs_by_gid: dict[str, list[db.Subscriber]] = {}
        for url, subs in by_group.items():
            gid = group_id(url)
            if gid:
                subs_by_gid.setdefault(gid, []).extend(subs)

        scrape_result = scrape(group_urls, dump_raw_keys=dump_raw_keys)
        posts = scrape_result.posts
        apify_run_id = scrape_result.apify_run_id
        posts_scraped = len(posts)

        for post in posts:
            subs = subs_by_gid.get(post.group_id or "", [])
            for sub in subs:
                keyword = matcher.first_match(post.text, sub.keywords)
                if not keyword:
                    continue
                # Idempotency guard: only proceed if this row is newly inserted.
                notification_log_id = db.log_notification(sub.id, post.url, keyword)
                if not notification_log_id:
                    continue
                matches_found += 1
                body = notifier.build_message(keyword, post.url)
                if sub.notify_sms and sub.phone:
                    notifier.send(sub.phone, keyword, post.url)
                    db.log_sendout(
                        subscriber_id=sub.id,
                        notification_log_id=notification_log_id,
                        phone=sub.phone,
                        body=body,
                        keyword=keyword,
                        post_url=post.url,
                        channel="simulated",
                        status="sent",
                    )
                    sent += 1
                else:
                    db.log_sendout(
                        subscriber_id=sub.id,
                        notification_log_id=notification_log_id,
                        phone=sub.phone or "",
                        body=body,
                        keyword=keyword,
                        post_url=post.url,
                        channel="simulated",
                        status="skipped",
                        error="notify_sms off or no phone",
                    )
                    log.info("Logged match for %s but SMS suppressed (opt-out/no phone)", sub.id)
        log.info(
            "Cycle complete: %d post(s) scraped, %d new match(es), %d SMS dispatched.",
            posts_scraped,
            matches_found,
            sent,
        )
    except Exception as exc:
        db.finish_engine_run(
            run_id,
            posts_scraped=posts_scraped,
            matches_found=matches_found,
            sms_dispatched=sent,
            apify_run_id=apify_run_id,
            error=str(exc),
        )
        raise
    else:
        db.finish_engine_run(
            run_id,
            posts_scraped=posts_scraped,
            matches_found=matches_found,
            sms_dispatched=sent,
            apify_run_id=apify_run_id,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="UGetFirst scraping/notification engine")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit.")
    parser.add_argument(
        "--dump-raw-keys",
        action="store_true",
        help="Log the keys of raw Apify items (to confirm output field names).",
    )
    args = parser.parse_args()

    log.info(
        "Starting engine (ENV=%s, project=%s, schema=%s, sms=SIMULATED->outbox/)",
        config.ENV,
        config.SUPABASE_URL,
        config.DB_SCHEMA,
    )

    if args.once:
        run_cycle(dump_raw_keys=args.dump_raw_keys)
        return

    health.mark_engine_started()

    while True:
        start = time.monotonic()
        try:
            run_cycle(dump_raw_keys=args.dump_raw_keys)
            health.mark_cycle_success()
        except Exception:
            log.exception("Cycle failed; will retry next interval.")
        elapsed = time.monotonic() - start
        sleep_for = max(0.0, config.MIN_INTERVAL_SECONDS - elapsed)
        log.info("Sleeping %.1fs until next cycle.", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
