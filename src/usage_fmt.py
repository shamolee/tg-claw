"""Usage formatting helpers, shared by commands.py and tg_claw_ctl.py."""

from datetime import datetime, timezone

from .messages import msg


def usage_bar(pct, emoji=True):
    """Format a 20-cell bar. With emoji=True, includes a colored icon."""
    filled = round(pct / 5)
    empty = 20 - filled
    bar = f"{'|' * filled}{'.' * empty} {pct:.0f}%"
    if emoji:
        if pct >= 90:
            icon = "\U0001f534"
        elif pct >= 70:
            icon = "\U0001f7e1"
        else:
            icon = "\U0001f7e2"
        return f"{icon} {bar}"
    return bar


def reset_str(bucket):
    """Time remaining until the reset, as a string."""
    reset_at = bucket.get("resets_at", "") if bucket else ""
    if not reset_at:
        return ""
    try:
        dt = datetime.fromisoformat(reset_at)
        remaining = (dt - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return msg("usage_reset_done")
        rm, rs = divmod(int(remaining), 60)
        rh, rm = divmod(rm, 60)
        rd, rh = divmod(rh, 24)
        if rd > 0:
            return f"({rd}d {rh}h {rm}m)"
        if rh > 0:
            return f"({rh}h {rm}m)"
        return f"({rm}m)"
    except Exception:
        return ""
