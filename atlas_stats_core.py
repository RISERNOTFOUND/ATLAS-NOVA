"""ATLAS's stat engine: storage, aggregation, formatting and charts (no Discord code, so it is testable)."""
import importlib
import io
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=5, minutes=30), name="IST")
PERIODS = {
    "24h": ("Last 24 hours", 24),
    "7d": ("Last 7 days", 24 * 7),
    "15d": ("Last 15 days", 24 * 15),
    "30d": ("Last 30 days", 24 * 30),
    "all": ("All time", None),
}
PERIOD_ALIASES = {"1d": "24h", "day": "24h", "24": "24h", "today": "24h", "week": "7d", "7": "7d", "15": "15d", "month": "30d",
                  "30": "30d", "alltime": "all", "all-time": "all", "lifetime": "all", "ever": "all"}
HOURLY_KEEP_HOURS = 24 * 31
NUM_KEYS = ("m", "r", "a", "x", "v", "u")        # messages, replies, attachments, reactions, voice sec, unmuted sec
DICT_KEYS = ("mc", "vc", "g")                      # per text-channel msgs, per voice-channel sec, per game sec


def parse_period(text, default="30d"):
    text = (text or "").strip().lower()
    if text in PERIODS:
        return text
    return PERIOD_ALIASES.get(text, default)


def hour_key(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), TZ).strftime("%Y%m%d%H")


def new_bucket():
    return {"m": 0, "r": 0, "a": 0, "x": 0, "v": 0, "u": 0, "mc": {}, "vc": {}, "g": {}}


def merge(into, src):
    for k in NUM_KEYS:
        into[k] = into.get(k, 0) + src.get(k, 0)
    for k in DICT_KEYS:
        target = into.setdefault(k, {})
        for name, val in src.get(k, {}).items():
            target[name] = target.get(name, 0) + val
    return into


def window_start_key(period):
    hours = PERIODS[period][1]
    return None if hours is None else hour_key(time.time() - hours * 3600)


class StatStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.dirty = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = data if isinstance(data, dict) and "guilds" in data else {"guilds": {}}
        except (OSError, ValueError):
            self.data = {"guilds": {}}

    # -------- structure
    def guild(self, gid):
        return self.data["guilds"].setdefault(str(gid), {"since": time.time(), "backfilled": False, "users": {}})

    def user(self, gid, uid):
        return self.guild(gid)["users"].setdefault(str(uid), {"h": {}, "mo": {}})

    def bucket(self, gid, uid, ts=None):
        self.dirty = True
        return self.user(gid, uid)["h"].setdefault(hour_key(ts), new_bucket())

    # -------- recording
    def add_message(self, gid, uid, cid, is_reply=False, attachments=0, ts=None):
        b = self.bucket(gid, uid, ts)
        b["m"] += 1
        b["r"] += 1 if is_reply else 0
        b["a"] += attachments
        b["mc"][str(cid)] = b["mc"].get(str(cid), 0) + 1

    def add_reaction(self, gid, uid):
        self.bucket(gid, uid)["x"] += 1

    def add_voice(self, gid, uid, cid, seconds, unmuted):
        b = self.bucket(gid, uid)
        b["v"] += int(seconds)
        b["u"] += int(seconds) if unmuted else 0
        b["vc"][str(cid)] = b["vc"].get(str(cid), 0) + int(seconds)

    def add_game(self, gid, uid, game, seconds):
        b = self.bucket(gid, uid)
        b["g"][game] = b["g"].get(game, 0) + int(seconds)

    # -------- persistence / housekeeping
    def rollup(self):
        """Hourly buckets older than 31 days are folded into monthly totals (kept forever)."""
        cutoff = hour_key(time.time() - HOURLY_KEEP_HOURS * 3600)
        for g in self.data["guilds"].values():
            for u in g["users"].values():
                for hk in [k for k in u["h"] if k < cutoff]:
                    merge(u["mo"].setdefault(hk[:6], new_bucket()), u["h"].pop(hk))
                    self.dirty = True

    def dumps(self):
        self.dirty = False
        return json.dumps(self.data, separators=(",", ":"))

    def write(self, payload):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)

    # -------- queries
    def user_total(self, gid, uid, period):
        u = self.guild(gid)["users"].get(str(uid))
        return self._sum_user(u, period) if u else new_bucket()

    def _sum_user(self, u, period):
        total, start = new_bucket(), window_start_key(period)
        for hk, b in u["h"].items():
            if start is None or hk >= start:
                merge(total, b)
        if start is None:
            for b in u["mo"].values():
                merge(total, b)
        return total

    def all_totals(self, gid, period):
        out = {}
        for uid, u in self.guild(gid)["users"].items():
            t = self._sum_user(u, period)
            if t["m"] or t["v"] or t["x"] or t["g"]:
                out[uid] = t
        return out

    def series(self, gid, uid, period):
        """(labels, messages[], voice_hours[]) for the chart. uid=None means whole server."""
        users = [self.guild(gid)["users"].get(str(uid))] if uid is not None else list(self.guild(gid)["users"].values())
        users = [u for u in users if u]
        now = datetime.now(TZ)
        if period == "24h":
            keys = [(now - timedelta(hours=i)).strftime("%Y%m%d%H") for i in range(23, -1, -1)]
            labels = [k[8:] for k in keys]
            agg = {k: [0, 0] for k in keys}
            for u in users:
                for hk, b in u["h"].items():
                    if hk in agg:
                        agg[hk][0] += b["m"]; agg[hk][1] += b["v"]
        elif period in ("7d", "15d", "30d"):
            days = PERIODS[period][1] // 24
            dates = [(now - timedelta(days=i)) for i in range(days - 1, -1, -1)]
            keys = [d.strftime("%Y%m%d") for d in dates]
            labels = [str(d.day) for d in dates]
            agg = {k: [0, 0] for k in keys}
            for u in users:
                for hk, b in u["h"].items():
                    if hk[:8] in agg:
                        agg[hk[:8]][0] += b["m"]; agg[hk[:8]][1] += b["v"]
        else:
            agg = {}
            for u in users:
                for mk, b in u["mo"].items():
                    a = agg.setdefault(mk, [0, 0]); a[0] += b["m"]; a[1] += b["v"]
                for hk, b in u["h"].items():
                    a = agg.setdefault(hk[:6], [0, 0]); a[0] += b["m"]; a[1] += b["v"]
            keys = sorted(agg)[-12:]
            labels = [datetime.strptime(k, "%Y%m").strftime("%b") for k in keys]
        return labels, [agg[k][0] for k in keys], [round(agg[k][1] / 3600, 2) for k in keys]

    def peak_hour_and_active_days(self, gid, uid, period):
        u = self.guild(gid)["users"].get(str(uid))
        if not u:
            return None, None
        start = window_start_key("30d" if period == "all" else period)
        score, days = {}, set()
        for hk, b in u["h"].items():
            if hk >= start and (b["m"] or b["v"]):
                score[int(hk[8:])] = score.get(int(hk[8:]), 0) + b["m"] + b["v"] / 600
                days.add(hk[:8])
        peak = max(score, key=score.get) if score else None
        limit = max(1, (PERIODS["30d" if period == "all" else period][1]) // 24)
        return peak, min(len(days), limit)


# ------------------------------------------------------------------ formatting
def fmt_dur(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def fmt_hour(h):
    return f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"


def bar(value, maximum, width=8):
    filled = 0 if maximum <= 0 else max(1 if value > 0 else 0, round(width * value / maximum))
    return "▰" * filled + "▱" * (width - filled)


def top(counter, n):
    return sorted(((k, v) for k, v in counter.items() if v > 0), key=lambda kv: kv[1], reverse=True)[:n]


def rank_of(totals, uid, key):
    ordered = sorted((t[key], u) for u, t in totals.items() if t[key] > 0)[::-1]
    for pos, (_, u) in enumerate(ordered, 1):
        if u == str(uid):
            return pos, len(ordered)
    return None, len(ordered)


# ------------------------------------------------------------------ charts
_chart_lock = threading.Lock()
BG, PANEL, TEXT, MUTED = "#1b1c28", "#242638", "#ecebf5", "#8b8da6"
PINK, CYAN, GOLD = "#ff7eb6", "#5cc8ff", "#ffd166"


def ensure_matplotlib(libs_dir):
    try:
        importlib.import_module("matplotlib")
        return True
    except Exception:
        pass
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--target", str(libs_dir), "matplotlib"])
        if str(libs_dir) not in sys.path:
            sys.path.insert(0, str(libs_dir))
        importlib.invalidate_caches()
        importlib.import_module("matplotlib")
        return True
    except Exception as exc:
        print(f"[Stats] could not install matplotlib, charts are off: {type(exc).__name__}: {exc}")
        return False


def safe_label(text, limit=16):
    cleaned = "".join(ch for ch in str(text) if ch.isprintable() and ord(ch) < 0x250).strip()
    return (cleaned or "user")[:limit]


def _style(ax):
    ax.set_facecolor(PANEL)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.grid(axis="y", color="#34364d", linewidth=0.6)
    ax.set_axisbelow(True)


def user_chart(labels, msgs, voice_hours):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    with _chart_lock:
        fig = Figure(figsize=(8.4, 3.1), dpi=150, facecolor=BG)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        _style(ax)
        x = list(range(len(labels)))
        ax.bar(x, msgs, color=PINK, width=0.62, alpha=0.92, label="Messages", zorder=2)
        ax.set_ylim(0, max(1, max(msgs, default=0)) * 1.3)
        ax.set_xticks(x)
        step = max(1, len(labels) // 12)
        ax.set_xticklabels([l if i % step == 0 else "" for i, l in enumerate(labels)])
        ax2 = ax.twinx()
        for side in ax2.spines.values():
            side.set_visible(False)
        ax2.tick_params(colors=CYAN, labelsize=8, length=0)
        ax2.plot(x, voice_hours, color=CYAN, linewidth=2.2, marker="o", markersize=3.4, label="Voice (hours)", zorder=3)
        ax2.fill_between(x, voice_hours, color=CYAN, alpha=0.12, zorder=1)
        ax2.set_ylim(0, max(0.5, max(voice_hours, default=0)) * 1.3)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        leg = ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False, fontsize=8, ncol=2)
        for t in leg.get_texts():
            t.set_color(TEXT)
        fig.subplots_adjust(left=0.05, right=0.95, top=0.93, bottom=0.12)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=BG)
        return buf.getvalue()


def server_chart(chatters, voicers):
    """chatters: [(name, messages)], voicers: [(name, hours)] - already sorted, best first."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    with _chart_lock:
        fig = Figure(figsize=(8.4, 3.4), dpi=150, facecolor=BG)
        FigureCanvasAgg(fig)
        for idx, (title, rows, colour) in enumerate((("Top chatters (messages)", chatters, PINK), ("Top VC sitters (hours)", voicers, CYAN))):
            ax = fig.add_subplot(1, 2, idx + 1)
            _style(ax)
            ax.grid(axis="x", color="#34364d", linewidth=0.6)
            ax.grid(axis="y", visible=False)
            ax.set_title(title, color=TEXT, fontsize=9, loc="left", pad=8)
            if rows:
                names = [safe_label(n) for n, _ in rows][::-1]
                vals = [v for _, v in rows][::-1]
                ax.barh(names, vals, color=colour, height=0.62, alpha=0.92, zorder=2)
                for i, v in enumerate(vals):
                    ax.text(v, i, f" {v:g}", va="center", color=TEXT, fontsize=7.5)
                ax.set_xlim(0, max(vals) * 1.25)
                ax.tick_params(axis="y", colors=TEXT, labelsize=8)
            else:
                ax.text(0.5, 0.5, "no data yet", ha="center", va="center", color=MUTED, transform=ax.transAxes)
                ax.set_xticks([]); ax.set_yticks([])
        fig.subplots_adjust(left=0.14, right=0.97, top=0.88, bottom=0.07, wspace=0.55)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=BG)
        return buf.getvalue()
