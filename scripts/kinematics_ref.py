"""Pure-Python port of picrawler 2.1.4 leg IK (read off the Pi).

Source of truth for the studio's reachability check; studio.html mirrors this
in JS. No hardware imports -- runs on the laptop. See memory picrawler_kinematics.md.
"""

import math

A = 48   # upper leg (mm)
B = 78   # lower leg (mm)
C = 33   # hip horizontal offset (mm)
LENGTH_SIDE = 77  # body side (mm)


def coord2polar(coord):
    """Foot [x,y,z] (leg-local frame) -> [alpha, beta, gamma] degrees.

    Verbatim port of Picrawler.coord2polar, including its internal L/u clamps.
    """
    x, y, z = coord
    L = math.sqrt(x ** 2 + y ** 2 + z ** 2)
    if L == 0:
        L = 0.1
    if L < C:
        t = C / L
        x, y, z = t * x, t * y, t * z
    elif L > (A + B + C):
        t = (A + B + C) / L
        x, y, z = t * x, t * y, t * z

    w = math.sqrt(x ** 2 + y ** 2)
    v = w - C
    u = math.sqrt(z ** 2 + v ** 2)
    u = max(30, min(91.58, u))

    beta = math.acos((B ** 2 + A ** 2 - u ** 2) / (2 * B * A))
    angle1 = math.atan2(z, v)
    angle2 = math.acos((A ** 2 + u ** 2 - B ** 2) / (2 * A * u))
    alpha = angle2 + angle1
    gamma = math.atan2(y, x)

    alpha = 90 - alpha / math.pi * 180
    beta = beta / math.pi * 180 - 90
    gamma = -(gamma / math.pi * 180 - 45)
    return [round(alpha, 4), round(beta, 4), round(gamma, 4)]


def is_reachable(coord):
    """True iff the coordinate commands the robot without any clamp/limit kicking in.

    Mirrors picrawler's positional clamps (L, u) and limit_angle bounds.
    """
    x, y, z = coord
    L = math.sqrt(x ** 2 + y ** 2 + z ** 2)
    if L == 0:
        return False
    if L < C or L > (A + B + C):
        return False
    w = math.sqrt(x ** 2 + y ** 2)
    v = w - C
    u = math.sqrt(z ** 2 + v ** 2)
    if u < 30 or u > 91.58:
        return False
    # picrawler sends servos as [beta, alpha, gamma] but limit_angle unpacks them as
    # (alpha, beta, gamma) -> the angle bounds are swapped vs coord2polar's names.
    alpha, beta, gamma = coord2polar(coord)
    return (-10 <= alpha <= 90) and (-90 <= beta <= 90) and (-60 <= gamma <= 60)
