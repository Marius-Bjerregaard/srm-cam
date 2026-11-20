"""The drill centres inside pads are MARKED in the copper, not burned
through the board. They were cut out of the copper before the isolation's
rings were drawn, so each 0.5 mm dot got rings of its own - the innermost a
few hundredths across, on one spot - at every pass and edge pass, and the
laser went through the board at every footprint hole (2026-11-20)."""
import math

from shapely.geometry import LineString, Point, box

from gerber2rml.engine import xtool

PADS = [(10.0, 10.0), (30.0, 10.0)]


def _art():
    copper = Point(*PADS[0]).buffer(1.0).union(Point(*PADS[1]).buffer(1.0))
    drills = [(x, y, 0.8) for x, y in PADS]
    return xtool.traces_art(copper, box(0, 0, 40, 20), drills, side="top",
                            mirror=False)


def _near_a_centre(ring, art, origin):
    """Does this bed ring lie within the 0.25 mm dot round a drill centre?"""
    _, h = art.size
    for x, y in PADS:
        bx, by = xtool.to_bed([(x, y)], origin, h)[0]
        if all(math.hypot(px - bx, py - by) <= 0.26 for px, py in ring):
            return True
    return False


def test_the_isolation_has_no_rings_inside_a_drill_dot():
    art = _art()
    groups, _outline = xtool.traces_rings(art, (50.0, 50.0))
    rings = [r for g in groups for r in g]
    assert rings
    assert not any(_near_a_centre(r, art, (50.0, 50.0)) for r in rings)


def test_each_centre_is_marked_with_one_ring_a_few_times():
    art = _art()
    origin = (50.0, 50.0)
    spots = xtool.spot_rings(art, origin)
    assert len(spots) == len(PADS)
    assert all(_near_a_centre(r, art, origin) for r in spots)
    job = xtool.traces_job(art, origin)
    marks = [t for t in job.timeline
             if t[3] >= xtool.DEFAULT_TRACES_RECIPE.passes
             + xtool.DEFAULT_TRACES_RECIPE.edge_passes]
    assert len(marks) == xtool.SPOT_PASSES
    assert xtool.SPOT_PASSES < xtool.DEFAULT_TRACES_RECIPE.passes


def test_marking_the_centres_can_be_turned_off():
    from dataclasses import replace
    art = _art()
    off = replace(xtool.DEFAULT_TRACES_RECIPE, spot_drills=False)
    assert xtool.spot_rings(art, (50.0, 50.0), off) == []


def test_a_rework_does_not_mark_them_again():
    art = _art()
    job = xtool.traces_job(art, (50.0, 50.0), boxes=[(40, 40, 120, 120)])
    assert len(job.timeline) <= xtool.DEFAULT_TRACES_RECIPE.passes


def test_burned_through_is_a_choice_with_rings_to_the_middle():
    from dataclasses import replace
    art = _art()
    origin = (50.0, 50.0)
    through = replace(xtool.DEFAULT_TRACES_RECIPE, drill_through=True)
    marked = xtool.spot_rings(art, origin)
    burned = xtool.spot_rings(art, origin, through)
    assert len(burned) > len(marked)              # filled in to the middle
    job = xtool.traces_job(art, origin, through)
    extra = [t for t in job.timeline
             if t[3] >= through.passes + through.edge_passes]
    assert len(extra) == through.passes           # every pass, as before


def test_a_selection_keeps_the_marks_on_its_trenches():
    """Auto-skip and a selection send the run a set of apertures: the marks
    on them are kept (a dot is an aperture of its own)."""
    art = _art()
    origin = (50.0, 50.0)
    every = set(range(len(art.apertures)))
    assert len(xtool.spot_rings(art, origin, only=every)) == len(PADS)


def test_burning_through_asks_for_the_fin_plate_first(qt_app, monkeypatch):
    """A run that burns the holes through asks for the fin plate under the
    board first, and does not start without it."""
    from PySide6.QtWidgets import QApplication   # noqa: F401 - qt_app
    from gerber2rml.gui2 import dialogs
    from gerber2rml.gui2 import stencil_laser as sl
    art = _art()
    asked = []
    monkeypatch.setattr(dialogs.Sheet, "exec",
                        lambda self: asked.append(self.objectName()) or 0)
    d = sl.StencilLaserDialog(art, laser=xtool.F1Ultra("127.0.0.1", 1,
                                                       timeout=1))
    try:
        d.spot_drills.setChecked(True)
        d.drill_through.setChecked(True)
        started = []
        monkeypatch.setattr(d, "_start", lambda *a, **k: started.append(1))
        d._fill()
        assert asked == ["finPlateSheet"] and not started
        asked.clear()
        d.drill_through.setChecked(False)         # marking only: no question
        d._fill()
        assert asked == []
    finally:
        d.close()
