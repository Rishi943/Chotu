"""Render a Chotu frames-JSON to a contact-sheet PNG (top + side per frame) for visual review.
Kinematic (geometry) only — Chotu is quasi-static; --stability draws the support polygon + CoM.
Run: python -m scripts.render_animation <f.json> [--stability] [--out PATH]"""
import argparse, json, math, pathlib, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scripts.kinematics_ref import A, B, C, LENGTH_SIDE

HALF = LENGTH_SIDE / 2
# (px, pz, sgn_x, sgn_y) per leg — mirror of studio CORNERS3D (FR,FL,RL,RR).
CORNERS = [(-HALF, HALF, -1, 1), (HALF, HALF, 1, 1), (HALF, -HALF, 1, -1), (-HALF, -HALF, -1, -1)]


def _legplane(x, y, z):  # mirror of studio.html legPlane; rigid A,B with u clamp
    L = math.sqrt(x * x + y * y + z * z) or 0.1
    if L < C: t = C / L; x, y, z = x * t, y * t, z * t
    elif L > A + B + C: t = (A + B + C) / L; x, y, z = x * t, y * t, z * t
    w = math.sqrt(x * x + y * y); a1 = math.atan2(z, w - C)
    u = min(max(math.hypot(w - C, z), 30), 91.58)
    th = a1 + math.acos((A * A + u * u - B * B) / (2 * A * u))
    return dict(kneeR=C + A * math.cos(th), kneeZ=A * math.sin(th),
                footR=C + u * math.cos(a1), footZ=u * math.sin(a1))


def joints(i, legs):
    x, y, z = legs[i]; pl = _legplane(x, y, z); px, pz, sx, sy = CORNERS[i]
    hx, hz = sx * x, sy * y; r = math.hypot(hx, hz) or 1e-6; dx, dz = hx / r, hz / r
    P = lambda rr, yy: (px + dx * rr, yy, pz + dz * rr)
    return P(0, 0), P(pl["kneeR"], pl["kneeZ"]), P(pl["footR"], pl["footZ"])


def _hull(pts):  # monotone chain on (X,Z)
    pts = sorted(set(pts))
    if len(pts) < 3: return pts
    cr = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo = []
    for p in pts:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(p)
    up = []
    for p in reversed(pts):
        while len(up) >= 2 and cr(up[-2], up[-1], p) <= 0: up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def _in_poly(pt, poly):
    if len(poly) < 3: return False
    x, z = pt; inside = False; n = len(poly); j = n - 1
    for i in range(n):
        xi, zi = poly[i]; xj, zj = poly[j]
        if ((zi > z) != (zj > z)) and (x < (xj - xi) * (z - zi) / (zj - zi + 1e-12) + xi): inside = not inside
        j = i
    return inside


def _planted_xz(legs):  # XZ of feet resting on the ground (lowest Y band)
    feet = [joints(i, legs)[2] for i in range(4)]
    miny = min(f[1] for f in feet)
    return [(f[0], f[2]) for f in feet if f[1] <= miny + 5]


def support_ok(legs):
    return _in_poly((0.0, 0.0), _hull(_planted_xz(legs)))  # body-center CoM projection in support polygon


def render(anim, out, stability=False):
    frames = anim["frames"]; n = len(frames)
    fig, axes = plt.subplots(2, n, figsize=(2.4 * n, 5), squeeze=False)
    for fi, f in enumerate(frames):
        legs = f["legs"]; top = axes[0][fi]; side = axes[1][fi]
        ok = support_ok(legs) if stability else True
        top.set_title(f"#{fi} h{f.get('hold_s', 0)}" + ("" if ok else " tip!"),
                      fontsize=8, color="black" if ok else "red")
        top.plot([-HALF, HALF, HALF, -HALF, -HALF], [HALF, HALF, -HALF, -HALF, HALF], "k-", lw=.6)  # body
        for i in range(4):
            h, k, ft = joints(i, legs)
            top.plot([h[0], k[0], ft[0]], [h[2], k[2], ft[2]], "-o", ms=2, lw=1)   # X vs Z
            side.plot([h[2], k[2], ft[2]], [h[1], k[1], ft[1]], "-o", ms=2, lw=1)  # Z(front) vs Y(up)
        if stability:
            poly = _hull(_planted_xz(legs))
            if len(poly) >= 3:
                xs = [p[0] for p in poly] + [poly[0][0]]; zs = [p[1] for p in poly] + [poly[0][1]]
                top.plot(xs, zs, "g-" if ok else "r-", lw=.8, alpha=.6)
            top.plot(0, 0, "go" if ok else "ro", ms=4)
        for ax in (top, side): ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    axes[0][0].set_ylabel("TOP", fontsize=8); axes[1][0].set_ylabel("SIDE", fontsize=8)
    fig.suptitle(anim.get("tool", ""), fontsize=10); fig.tight_layout()
    out = pathlib.Path(out); fig.savefig(out, dpi=90); plt.close(fig); return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("file"); ap.add_argument("--stability", action="store_true"); ap.add_argument("--out")
    ns = ap.parse_args(argv)
    fp = pathlib.Path(ns.file); anim = json.loads(fp.read_text())
    out = render(anim, ns.out or fp.with_suffix(".preview.png"), stability=ns.stability)
    print(f"wrote {out}"); return 0


if __name__ == "__main__": sys.exit(main())
