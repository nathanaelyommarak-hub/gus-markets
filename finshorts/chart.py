"""Matplotlib chart from API data only."""
import datetime as dt
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
from pathlib import Path

FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Inter.ttf"
COL = {"green": "#22E39B", "red": "#FF4D5E", "neutral": "#FF8A3D"}   # brand mint / coral / Gus orange


def _parse(t):
    t = str(t)
    return dt.datetime.fromisoformat(t[:19]) if "T" in t else dt.datetime.fromisoformat(t[:10])


def render_chart(points, title, subtitle, mood, out_png, value_fmt="{:,.0f}", w=1000, h=560, source=""):
    if FONT.exists():
        font_manager.fontManager.addfont(str(FONT))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT)).get_name()
    xs = [_parse(p[0]) for p in points]; ys = [p[1] for p in points]
    c = COL[mood]
    fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
    fig.patch.set_alpha(0)
    ax = fig.add_axes([0.03, 0.14, 0.82, 0.56])   # top ~30% stays free: renderer draws big number + signal there
    ax.set_facecolor((0, 0, 0, 0))
    ax.plot(xs, ys, color=c, lw=5, solid_capstyle="round")
    lo = min(ys); pad = (max(ys) - lo) * 0.08 or 1
    ax.fill_between(xs, ys, lo - pad, color=c, alpha=0.18)
    ax.set_ylim(lo - pad, max(ys) + pad)
    ax.scatter([xs[-1]], [ys[-1]], s=220, color=c, zorder=5, edgecolor="white", linewidth=3)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors="#9fb0d0", labelsize=17, length=0)
    ax.yaxis.tick_right()
    ax.grid(axis="y", color="#ffffff", alpha=0.08, lw=1)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=5))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: value_fmt.format(v)))
    if source:
        fig.text(0.04, 0.02, source, color="#7f8cab", fontsize=14)
    fig.savefig(out_png, transparent=True)
    plt.close(fig)
    return out_png
