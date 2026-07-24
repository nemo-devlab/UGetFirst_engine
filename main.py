"""UGetFirst engine: poll Facebook groups, match keywords, send SMS/email.

Runs one cycle synchronously, then sleeps so cycles never overlap and never
start more often than MIN_INTERVAL_SECONDS. Per-group cadence skips groups
that are not due yet. Idempotency is guaranteed by the unique
(subscriber_id, post_url) constraint on notification_logs.
"""
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass

import config
import db
import health
import matcher
import notifier
from scraper import Post, group_id, scrape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ugetfirst")


@dataclass
class DispatchStats:
    matches_found: int = 0
    alerts_dispatched: int = 0


def dispatch_posts(
    posts: list[Post],
    subscribers_by_group_id: dict[str, list[db.Subscriber]],
    *,
    channels: set[str] | None = None,
) -> DispatchStats:
    """Run the production match, dedup, notify, and sendout-log path.

    ``channels`` is used only by the DEV test harness to request email, SMS, or
    both. Normal engine cycles pass no filter.
    """
    if channels is not None and not channels <= {"sms", "email"}:
        raise ValueError(f"Unsupported channel filter: {sorted(channels)!r}")

    stats = DispatchStats()
    sends_by_sub: dict[str, int] = {}
    max_per_sub = config.SMS_MAX_PER_SUBSCRIBER_PER_CYCLE
    delay_s = max(0, config.SMS_SEND_DELAY_MS) / 1000.0

    for post in posts:
        subs = subscribers_by_group_id.get(post.group_id or "", [])
        for sub in subs:
            keyword = matcher.first_match(post.text, sub.keywords)
            if not keyword:
                continue
            notification_log_id = db.log_notification(sub.id, post.url, keyword)
            if not notification_log_id:
                continue
            stats.matches_found += 1
            body = notifier.build_message(keyword, post.url)

            sms_selected = channels is None or "sms" in channels
            email_selected = channels is None or "email" in channels
            sms_ready = sms_selected and sub.sms_ok and bool(sub.phone)
            email_ready = email_selected and sub.email_ok and bool(sub.email)

            if not sms_ready and not email_ready:
                db.log_sendout(
                    subscriber_id=sub.id,
                    notification_log_id=notification_log_id,
                    phone=sub.phone or "",
                    body=body,
                    keyword=keyword,
                    post_url=post.url,
                    channel="simulated",
                    status="skipped",
                    error="no_alert_channel",
                )
                continue

            already = sends_by_sub.get(sub.id, 0)
            if max_per_sub > 0 and already >= max_per_sub:
                db.log_sendout(
                    subscriber_id=sub.id,
                    notification_log_id=notification_log_id,
                    phone=sub.phone or "",
                    body=body,
                    keyword=keyword,
                    post_url=post.url,
                    channel="simulated",
                    status="skipped",
                    error="rate_limited_cycle",
                )
                continue

            channel_sent = False

            if sms_ready and sub.phone:
                if delay_s > 0 and stats.alerts_dispatched > 0:
                    time.sleep(delay_s)
                result = notifier.send(sub.phone, keyword, post.url)
                db.log_sendout(
                    subscriber_id=sub.id,
                    notification_log_id=notification_log_id,
                    phone=sub.phone,
                    body=body,
                    keyword=keyword,
                    post_url=post.url,
                    channel=result.channel,
                    status=result.status,
                    provider_message_id=result.provider_message_id,
                    error=result.error,
                )
                if result.status == "sent":
                    stats.alerts_dispatched += 1
                    channel_sent = True

            if email_ready and sub.email:
                if delay_s > 0 and stats.alerts_dispatched > 0:
                    time.sleep(delay_s)
                email_body, _ = notifier.build_email_bodies(keyword, post.url)
                result = notifier.send_email_alert(sub.email, keyword, post.url)
                db.log_sendout(
                    subscriber_id=sub.id,
                    notification_log_id=notification_log_id,
                    phone=sub.phone or "",
                    body=email_body,
                    keyword=keyword,
                    post_url=post.url,
                    channel=result.channel,
                    status=result.status,
                    provider_message_id=result.provider_message_id,
                    error=result.error,
                )
                if result.status == "sent":
                    stats.alerts_dispatched += 1
                    channel_sent = True

            if channel_sent:
                sends_by_sub[sub.id] = already + 1

    return stats


def run_cycle(dump_raw_keys: bool = False) -> None:
    run_id: str | None = None
    posts_scraped = 0
    matches_found = 0
    sent = 0
    apify_run_id: str | None = None

    try:
        all_groups = db.load_monitoring()
        if not all_groups:
            log.info("No groups with alert-ready subscribers; nothing to do.")
            run_id = db.start_engine_run(0)
            db.finish_engine_run(run_id)
            return

        by_group, due_count, skipped = db.filter_due_groups(all_groups)
        log.info(
            "Cadence: %d due / %d total (%d not due yet)",
            due_count,
            len(all_groups),
            skipped,
        )
        if not by_group:
            run_id = db.start_engine_run(0)
            db.finish_engine_run(run_id)
            return

        group_urls = list(by_group.keys())
        run_id = db.start_engine_run(len(group_urls))
        lookback = db.max_lookback_for_groups(by_group)

        # Index subscribers by numeric group id for robust post -> group matching.
        # Match against all alert-ready watchers (not only due-cycle set) so a
        # Free rider on a Lightning-forced scrape still gets email.
        subs_by_gid: dict[str, list[db.Subscriber]] = {}
        for url, subs in all_groups.items():
            gid = group_id(url)
            if gid:
                subs_by_gid.setdefault(gid, []).extend(subs)

        scrape_result = scrape(
            group_urls, dump_raw_keys=dump_raw_keys, lookback=lookback
        )
        posts = scrape_result.posts
        apify_run_id = scrape_result.apify_run_id
        posts_scraped = len(posts)

        db.mark_groups_scraped(group_urls)

        # Accumulate scrape results as a data asset (independent of matches).
        db.upsert_scraped_posts(
            posts,
            engine_run_id=run_id,
            apify_run_id=apify_run_id,
        )

        stats = dispatch_posts(posts, subs_by_gid)
        matches_found = stats.matches_found
        sent = stats.alerts_dispatched

        log.info(
            "Cycle complete: %d post(s) scraped, %d new match(es), %d alert(s) dispatched.",
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
        "Starting engine (ENV=%s, project=%s, schema=%s, sms=%s, resend=%s)",
        config.ENV,
        config.SUPABASE_URL,
        config.DB_SCHEMA,
        config.SMS_MODE,
        "on" if config.RESEND_API_KEY else "outbox",
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
