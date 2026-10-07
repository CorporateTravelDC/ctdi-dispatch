"""common.optime -- the one place that answers "what operational day is it?".

Operator design (2026-10-04, backlog #9): containers run in UTC on purpose
(logs and epoch maths are correct that way) and storage stays UTC epoch, but
anything DAY-KEYED -- a vault day file, a digest window, a heading date --
must follow the operator's local calendar. Before this module each skill
called date.today(), which inside a UTC container is the UTC date: the
23:45 ET second-brain-daily anchor wrote the NEXT day's file every night,
and the daily-watch skills labelled late-evening runs with tomorrow's date.

Two non-secret settings, read from the environment (dispatch.env):
  DISPATCH_LOCAL_TZ      IANA zone, default America/New_York
  DISPATCH_DAY_ROLLOVER  HH:MM local; earlier hours belong to the previous
                         operational day, default 05:00 (the end of the
                         23:00-05:00 maintenance window)

Rules:
  * op_now()/op_today()/resolve_target_date() are local and rollover-aware;
  * op_day_window(day) is [local midnight, next local midnight) as UTC epoch
    seconds -- use it for every "today's rows" query;
  * to_local()/to_utc() convert at the edges; naive datetimes passed in are
    treated as UTC (the container's clock), never as local.
tests/common/test_no_naive_dates.py fails the build on new bare
date.today() / naive datetime.now() / utcnow() in skills and ingest.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/New_York"
DEFAULT_ROLLOVER = "05:00"


def local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("DISPATCH_LOCAL_TZ") or DEFAULT_TZ)


def rollover() -> dtime:
    raw = (os.environ.get("DISPATCH_DAY_ROLLOVER") or DEFAULT_ROLLOVER).strip()
    h, _, m = raw.partition(":")
    return dtime(int(h), int(m or 0))


def _aware_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def op_now(now_utc: datetime | None = None) -> datetime:
    """Current local wall time (aware)."""
    return _aware_utc(now_utc or datetime.now(timezone.utc)).astimezone(local_tz())


def resolve_target_date(now_utc: datetime | None = None, override: str | None = None) -> date:
    """The operational day: explicit ISO override wins; otherwise the local
    date, minus one day when the local time is before the rollover."""
    if override:
        return date.fromisoformat(override.strip())
    local = op_now(now_utc)
    d = local.date()
    if local.time() < rollover():
        d -= timedelta(days=1)
    return d


def op_today(now_utc: datetime | None = None) -> date:
    """Alias with the conventional name; rollover-aware."""
    return resolve_target_date(now_utc)


def op_day_window(day: date) -> tuple[float, float]:
    """[local midnight of `day`, local midnight of the next day) as UTC epoch
    seconds. DST-correct (23h / 25h days come out as such)."""
    tz = local_tz()
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start.timestamp(), end.timestamp()


def to_local(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=local_tz())


def to_utc(local_dt: datetime) -> datetime:
    """Local wall time -> aware UTC. A naive input is interpreted as LOCAL here
    (this is the one function whose purpose is local -> UTC)."""
    if local_dt.tzinfo is None:
        local_dt = local_dt.replace(tzinfo=local_tz())
    return local_dt.astimezone(timezone.utc)
