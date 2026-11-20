"""Tests for the ramped lead-in (engine/leadin.py)."""
import math
from gerber2rml.toolpath import Move
from gerber2rml.engine.leadin import apply_lead_in

CZ = -0.15            # cut depth (negative Z)


def _square_ring(side=10.0, travel=2.0):
    c = [(0, 0), (side, 0), (side, side), (0, side), (0, 0)]
    tp = [Move(0, 0, travel, rapid=True), Move(0, 0, CZ)]
    tp += [Move(x, y, CZ) for (x, y) in c[1:]]
    tp.append(Move(0, 0, travel, rapid=True))
    return tp


def _cuts(tp):
    return [m for m in tp if not m.rapid]


def test_drill_plunge_is_left_alone():
    # rapid down, plunge straight to depth, retract -- no lateral cut to ramp on.
    drill = [Move(5, 5, 2.0, rapid=True), Move(5, 5, -1.8),
             Move(5, 5, 2.0, rapid=True)]
    assert apply_lead_in([drill])[0] == drill


def test_pecking_plunge_is_left_alone():
    peck = [Move(5, 5, 2.0, rapid=True), Move(5, 5, -0.6), Move(5, 5, -1.2),
            Move(5, 5, -1.8), Move(5, 5, 2.0, rapid=True)]
    assert apply_lead_in([peck])[0] == peck


def test_entry_is_no_longer_a_full_vertical_plunge():
    out = apply_lead_in([_square_ring()], ramp_len=2.0)[0]
    # No move should drop straight to full depth at constant XY (the old plunge).
    prev = None
    for m in out:
        if prev is not None and not m.rapid:
            same_xy = abs(m.x - prev.x) < 1e-9 and abs(m.y - prev.y) < 1e-9
            assert not (same_xy and abs(m.z - CZ) < 1e-9 and prev.z > CZ + 1e-9), \
                "found a vertical plunge straight to full depth"
        prev = m


def test_first_cut_moves_descend_gradually_to_full_depth():
    out = apply_lead_in([_square_ring()], ramp_len=4.0)[0]
    cz_seq = [m.z for m in _cuts(out)]
    # starts shallower than full depth and reaches full depth, monotonic down
    assert cz_seq[0] > CZ + 1e-9
    assert min(cz_seq) <= CZ + 1e-9
    ramp = []
    for z in cz_seq:
        ramp.append(z)
        if z <= CZ + 1e-9:
            break
    assert all(ramp[i] >= ramp[i + 1] - 1e-9 for i in range(len(ramp) - 1))


def test_ring_is_still_fully_cut_at_depth():
    out = apply_lead_in([_square_ring()], ramp_len=2.0)[0]
    assert abs(min(m.z for m in out) - CZ) < 1e-9          # never deeper than cut_z
    # every original corner is cut at full depth somewhere in the path
    at_depth = {(round(m.x, 3), round(m.y, 3)) for m in out
                if not m.rapid and abs(m.z - CZ) < 1e-9}
    for corner in [(0, 0), (10, 0), (10, 10), (0, 10)]:
        assert corner in at_depth


def test_short_ring_shorter_than_ramp_still_valid():
    out = apply_lead_in([_square_ring(side=0.3)], ramp_len=5.0)[0]
    assert abs(min(m.z for m in out) - CZ) < 1e-9          # reaches full depth
    assert out[0].rapid                                     # still starts with a rapid


def test_fast_approach_rapids_down_and_feeds_only_the_last_bit():
    from gerber2rml.engine.leadin import fast_approach, FAST_APPROACH
    from gerber2rml.toolpath import Move
    ring = [Move(0, 0, 2.0, rapid=True), Move(0, 0, -0.08),
            Move(1, 0, -0.08), Move(1, 0, 2.0, rapid=True)]
    out = fast_approach([ring])[0]
    assert out[1] == Move(0, 0, FAST_APPROACH, rapid=True)
    assert out[2] == Move(0, 0, -0.08)
    assert out[3:] == ring[2:]


def test_fast_approach_leaves_peck_reentries_alone():
    from gerber2rml.engine.leadin import fast_approach
    from gerber2rml.toolpath import Move
    peck = [Move(0, 0, 0.2, rapid=True), Move(0, 0, -1.0),
            Move(0, 0, -0.8, rapid=True), Move(0, 0, -1.9)]
    assert fast_approach([peck])[0] == peck


def _line(x1, travel=2.0, y=0.0):
    """An open cut from (0, y) to (x1, y): a gap cut, or a piece of cut-out
    between two tabs."""
    return [Move(0, y, travel, rapid=True), Move(0, y, CZ), Move(x1, y, CZ),
            Move(x1, y, travel, rapid=True)]


def _full_depth_cover(tp, z=CZ):
    """The stretches of x the path cuts at full depth, merged."""
    spans = sorted((min(a.x, b.x), max(a.x, b.x)) for a, b in zip(tp, tp[1:])
                   if not b.rapid and abs(a.z - z) < 1e-9
                   and abs(b.z - z) < 1e-9)
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def test_an_open_cut_shorter_than_the_ramp_is_cut_full_depth_end_to_end():
    """A 0.5 mm gap cut with the 1 mm ramp: it used to ramp from end to end,
    reaching full depth only at its last point (2026-11 review)."""
    out = apply_lead_in([_line(0.5)])[0]
    assert _full_depth_cover(out) == [[0.0, 0.5]]
    assert min(m.z for m in out) >= CZ - 1e-9
    last_cut = [m for m in out if not m.rapid][-1]
    assert (last_cut.x, last_cut.y) == (0.5, 0.0)   # retract where it ends


def test_an_open_cut_is_full_depth_over_its_ramp_too():
    out = apply_lead_in([_line(5.0)])[0]
    assert _full_depth_cover(out) == [[0.0, 5.0]]
    # ...and it still enters on a ramp, not a plunge to depth
    cuts = [m for m in out if not m.rapid]
    assert cuts[0].z > 0 and cuts[1].z > CZ


def test_cut_out_pieces_between_tabs_are_cut_through_but_tabs_are_not():
    """The cut-out's last pass: every piece between two tabs is cut to the
    full depth all along, and the tabs are left exactly the width set. The
    ramp at each piece's start used to widen the tab by up to the ramp and
    leave a piece under 1 mm not cut through."""
    from shapely.geometry import LineString, box
    from shapely.ops import unary_union
    from gerber2rml.config import CutoutJob
    from gerber2rml.engine.cutout import cut_outline
    job = CutoutJob(bit_diameter=0.8, cut_depth=0.6, total_depth=1.7, tabs=4,
                    tab_width=1.5)
    raw = cut_outline(box(0, 0, 20, 12), job)
    out = apply_lead_in(raw)
    zf = -job.total_depth
    for before, after in zip(raw, out):
        body = LineString([(m.x, m.y) for m in before if not m.rapid])
        if abs(min(m.z for m in before) - zf) > 1e-9:
            continue                                   # not the last pass
        full = unary_union([LineString([(a.x, a.y), (b.x, b.y)])
                            for a, b in zip(after, after[1:])
                            if not b.rapid and abs(a.z - zf) < 1e-9
                            and abs(b.z - zf) < 1e-9
                            and (a.x, a.y) != (b.x, b.y)])
        assert body.difference(full.buffer(1e-6)).length < 1e-6
        # nothing outside the piece: the tab is not eaten into
        assert full.difference(body.buffer(1e-6)).length < 1e-6
