"""Pins the picrawler IK port. The JS in studio.html must reproduce these."""

from scripts.animation.kinematics_ref import coord2polar, is_reachable, A, B, C


def test_constants_match_picrawler():
    assert (A, B, C) == (48, 78, 33)


def test_coord2polar_returns_three_angles():
    angles = coord2polar([60, 0, -30])  # stand
    assert len(angles) == 3


def test_stand_gamma_is_45():
    # y=0 -> foot points straight forward -> gamma = -(0 - 45) = 45
    _, _, gamma = coord2polar([60, 0, -30])
    assert abs(gamma - 45.0) < 0.01


def test_stand_is_reachable():
    assert is_reachable([60, 0, -30]) is True


def test_far_reach_clamps_so_not_reachable():
    # L = sqrt(100^2+130^2+60^2) ~= 174.6 > A+B+C (159) -> robot would clamp
    assert is_reachable([100, 130, -60]) is False


def test_wave_exceeds_u_limit():
    # [0,120,60] drives u beyond 91.58 -> clamped on hardware
    assert is_reachable([0, 120, 60]) is False
