"""Provider-independent values. Missing quota is never interpreted as zero."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math


@dataclass(frozen=True)
class Quota:
    label: str
    used_percent: float
    resets_at: datetime | None = None

    def __post_init__(self):
        if not math.isfinite(self.used_percent) or not 0 <= self.used_percent <= 100:
            raise ValueError("Quota percentage must be between 0 and 100")

    @property
    def remaining_percent(self) -> float:
        return 100 - self.used_percent

    def reset_text(self) -> str:
        if self.resets_at is None:
            return "Reset time unavailable"
        seconds = (self.resets_at - datetime.now(timezone.utc)).total_seconds()
        if seconds <= 0:
            return "Reset due · awaiting refresh"
        minutes = max(1, math.ceil(seconds / 60))
        days, hours = divmod(minutes // 60, 24)
        if days:
            return f"Resets in {days}d {hours}h"
        if hours:
            return f"Resets in {hours}h {minutes % 60}m"
        return f"Resets in {minutes}m"


@dataclass(frozen=True)
class Snapshot:
    provider: str
    product: str
    quotas: tuple[Quota, ...] = ()
    status: str = "Not connected"
    detail: str = ""
    fetched_at: datetime | None = None
    stale: bool = False

    @property
    def headline(self) -> Quota | None:
        """The most constrained window: the one that will block you first."""
        return max(self.quotas, key=lambda quota: quota.used_percent, default=None)


def countdown_text(resets_at: datetime | None, now: datetime | None = None) -> str:
    """Compact live countdown: '2d 14h', '3h 05m', '48m 09s'."""
    if resets_at is None:
        return "—"
    seconds = int((resets_at - (now or datetime.now(timezone.utc))).total_seconds())
    if seconds <= 0:
        return "due"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {seconds:02d}s"


def percent_text(value: float) -> str:
    """Whole percent that never rounds a partial window to empty or exhausted."""
    shown = round(value)
    if shown == 100 and value < 100:
        return "99"
    if shown == 0 and value > 0:
        return "<1"
    return str(shown)


def usage_tone(used_percent: float) -> str:
    return "critical" if used_percent >= 85 else "warning" if used_percent >= 65 else "normal"


def parse_percent(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= 100 else None


def parse_time(value) -> datetime | None:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, timezone.utc)
        if isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def parse_codex(data: dict) -> tuple[Quota, ...]:
    by_id = data.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and by_id:
        limits = list(by_id.items())
    else:
        limits = [("codex", data.get("rateLimits"))]
    rows = []
    for limit_id, limit in limits:
        if not isinstance(limit, dict):
            continue
        for key in ("primary", "secondary"):
            window = limit.get(key)
            if not isinstance(window, dict):
                continue
            used = parse_percent(window.get("usedPercent"))
            if used is None:
                continue
            mins = window.get("windowDurationMins")
            if isinstance(mins, (int, float)) and mins > 0:
                label = f"{mins / 1440:g}-day" if mins >= 1440 else f"{mins / 60:g}-hour" if mins >= 60 else f"{mins:g}-minute"
            else:
                label = key.capitalize()
            if limit_id != "codex":
                label = f"{limit.get('limitName') or limit_id} · {label}"
            rows.append(Quota(label, used, parse_time(window.get("resetsAt"))))
    return tuple(rows)


def parse_claude(data: dict) -> tuple[Quota, ...]:
    labels = {"five_hour": "5-hour", "seven_day": "Weekly", "seven_day_sonnet": "Sonnet · weekly", "seven_day_opus": "Opus · weekly"}
    rows = []
    for key, value in data.items():
        if key == "extra_usage" or not isinstance(value, dict):
            continue
        used = parse_percent(value.get("utilization"))
        if used is not None:
            rows.append(Quota(labels.get(key, key.replace("_", " ").capitalize()), used, parse_time(value.get("resets_at"))))
    return tuple(rows)


def parse_gemini(data: dict) -> tuple[Quota, ...]:
    rows = []
    buckets = data.get("buckets")
    if not isinstance(buckets, list):
        return ()
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        fraction = bucket.get("remainingFraction")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1:
            continue
        name = bucket.get("modelId") or "Model quota"
        if bucket.get("tokenType"):
            name += f" · {bucket['tokenType']}"
        rows.append(Quota(name, round((1-fraction)*100, 4), parse_time(bucket.get("resetTime"))))
    return tuple(rows)


def parse_antigravity(data: dict) -> tuple[Quota, ...]:
    """Read shared quota pools, deduplicating models that share a bucket."""
    root = data.get("response", data)
    groups = root.get("groups") if isinstance(root, dict) else None
    if not isinstance(groups, list):
        return ()
    labels = {
        "gemini-5h": "Gemini · 5-hour", "gemini-weekly": "Gemini · weekly",
        "3p-5h": "Claude & GPT · 5-hour", "3p-weekly": "Claude & GPT · weekly",
    }
    rows, seen = [], set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("buckets"), list):
            continue
        for bucket in group["buckets"]:
            if not isinstance(bucket, dict):
                continue
            bucket_id = bucket.get("bucketId")
            if not isinstance(bucket_id, str) or not bucket_id or bucket_id in seen:
                continue
            # An omitted flag means enabled; malformed flags must not show a quota.
            disabled = bucket.get("disabled")
            if disabled is not None and disabled is not False:
                continue
            fraction = bucket.get("remainingFraction")
            if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1:
                continue
            name = labels.get(bucket_id)
            if name is None:
                group_name = group.get("displayName")
                name = group_name if isinstance(group_name, str) and group_name else bucket_id
                window = bucket.get("window")
                if isinstance(window, str) and window:
                    name += f" · {window}"
            rows.append(Quota(name, round((1-fraction)*100, 4), parse_time(bucket.get("resetTime"))))
            seen.add(bucket_id)
    return tuple(rows)
