"""EXPERIMENTAL: the laser cuts the board out along Edge.Cuts - off by
default, last in the job, in the waste outside the outline (and inside a
window), ``cut_passes`` times, and the page asks for the fin plate first."""
import dataclasses

from shapely.geometry import Point, Polygon, box

from gerber2rml.engine import xtool

ORIGIN = (50.0, 50.0)


def _art(outline=None):
    copper = Point(10, 10).buffer(1.0)
    return xtool.traces_art(copper, outline or box(0, 0, 40, 20), [],
                            side="top", mirror=False)


def _recipe(**kw):
    return dataclasses.replace(xtool.DEFAULT_TRACES_RECIPE, **kw)


def test_off_by_default_nothing_is_cut():
    assert xtool.DEFAULT_TRACES_RECIPE.cut_outline is False
    plain = xtool.traces_job(_art(), ORIGIN, write=False)
    assert not any(t[3] >= plain.passes + 40 for t in plain.timeline)


def test_the_cut_runs_last_cut_passes_times_at_full_power():
    art = _art()
    off = xtool.traces_job(art, ORIGIN, _recipe(), write=False)
    on = xtool.traces_job(art, ORIGIN, _recipe(cut_outline=True,
                                               cut_passes=7), write=False)
    extra = on.timeline[len(off.timeline):]
    assert len(extra) == 7
    assert on.seconds > off.seconds
    assert extra[0][0] >= off.seconds - 1e-6


def test_the_rings_lie_in_the_waste_outside_the_board():
    art = _art()
    r = _recipe(cut_outline=True, cut_lines=3)
    rings = xtool.cut_rings(art, ORIGIN, r)
    assert len(rings) == 3
    _, h = art.size
    board = Polygon(xtool.to_bed([(0, 0), (40, 0), (40, 20), (0, 20)],
                                 ORIGIN, h))
    for ring in rings:
        assert not Polygon(ring).buffer(-1e-6).within(board)
        assert Polygon(ring).contains(board)


def test_a_window_is_cut_too_inside_it():
    outline = box(0, 0, 40, 20).difference(box(15, 5, 25, 15))
    art = _art(outline)
    rings = xtool.cut_rings(art, ORIGIN, _recipe(cut_outline=True,
                                                 cut_lines=1))
    assert len(rings) == 2          # the edge and the window


def test_no_cut_on_a_rework():
    art = _art()
    r = _recipe(cut_outline=True, cut_passes=5)
    plain = xtool.traces_job(art, ORIGIN, r, write=False)
    rework = xtool.traces_job(art, ORIGIN, r, boxes=[(0, 0, 200, 200)],
                              write=False)
    assert len(rework.timeline) < len(plain.timeline)


def test_the_file_has_the_cut():
    art = _art()
    off = xtool.traces_job(art, ORIGIN, _recipe())
    on = xtool.traces_job(art, ORIGIN, _recipe(cut_outline=True,
                                               cut_passes=3))
    assert len(on.text) > len(off.text)


def test_the_cut_asks_for_the_fin_plate_first(qt_app, monkeypatch):
    from gerber2rml.gui2 import dialogs
    from gerber2rml.gui2 import stencil_laser as sl
    asked = []
    monkeypatch.setattr(dialogs.Sheet, "exec",
                        lambda self: asked.append(self.objectName()) or 0)
    d = sl.StencilLaserDialog(_art(), laser=xtool.F1Ultra("127.0.0.1", 1,
                                                          timeout=1))
    try:
        assert not d.cut_passes.isEnabled()
        d.cut_outline.setChecked(True)
        assert d.cut_passes.isEnabled()
        d.cut_passes.setValue(12)
        r = d._run_recipe()
        assert r.cut_outline and r.cut_passes == 12
        started = []
        monkeypatch.setattr(d, "_start", lambda *a, **k: started.append(1))
        d._fill()
        assert asked == ["finPlateSheet"] and not started
        asked.clear()
        d.cut_outline.setChecked(False)
        d._fill()
        assert asked == []
    finally:
        d.close()
