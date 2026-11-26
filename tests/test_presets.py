"""Test presets: built-in, repo example, and user presets."""
import pytest

from gerber2rml.app.presets import load_presets, apply_preset, save_user_preset, BUILTIN_PRESETS
from gerber2rml.app.state import ProjectState


def test_builtin_presets_are_the_flat_and_ball_mill_profiles():
    # We ship two profiles: the 0.8 mm flat endmill (default, first) and the
    # 0.8 mm ball mill for fine SMD work.
    assert len(BUILTIN_PRESETS) == 2
    names = list(BUILTIN_PRESETS)
    assert all(n.startswith("SRM-20") for n in names)
    assert names[0].startswith("SRM-20 0.8 mm flat")     # default stays first
    assert names[1].startswith("SRM-20 0.8 mm ball mill")


def test_ball_mill_preset_holds_the_measured_settings():
    ball = BUILTIN_PRESETS[list(BUILTIN_PRESETS)[1]]
    assert ball["trace"]["tool_type"] == "ball"     # the ball's own size...
    assert ball["trace"]["bit_diameter"] == 0.8     # ...its channel worked out
    # 0.08 was the depth measured on 4 Nov; it did not come through the
    # copper everywhere on a real sheet, so the profile cuts 0.11.
    assert ball["trace"]["cut_depth"] == 0.11
    assert ball["trace"]["cut_pinches"] is True
    assert ball["drill"]["bit_diameter"] == 0.8     # real holes and outline
    assert ball["drill"]["total_depth"] == 2.0
    assert ball["cutout"]["total_depth"] == 1.9
    assert ball["cutout"]["tabs"] == 4


def test_ball_mill_preset_applies_every_field():
    st = ProjectState()
    apply_preset(st, BUILTIN_PRESETS[list(BUILTIN_PRESETS)[1]])
    assert st.trace.tool_type == "ball"
    # Deeper than the measured 0.08 mm, so wider than its 0.44 mm channel.
    assert st.trace.effective_diameter() == pytest.approx(0.505, abs=0.001)
    assert st.trace.cut_pinches is True
    assert st.drill.bit_diameter == 0.8
    assert st.cutout.bit_diameter == 0.8


def test_srm20_preset_depths_and_feeds():
    name = next(iter(BUILTIN_PRESETS))         # first = the default in the GUI
    p = BUILTIN_PRESETS[name]
    assert p["trace"]["bit_diameter"] == 0.8   # one 0.8 mm bit for everything
    assert p["trace"]["xy_feed"] == 4.0
    assert p["drill"]["total_depth"] == 2.0    # 1.9 left holes not quite through
    assert p["cutout"]["total_depth"] == 2.0


def test_apply_preset_sets_jobs():
    st = ProjectState()
    name = next(iter(BUILTIN_PRESETS))
    apply_preset(st, BUILTIN_PRESETS[name])
    assert st.trace.bit_diameter == 0.8
    assert st.cutout.tabs == 4


def test_save_and_load_user_preset(tmp_path, monkeypatch):
    monkeypatch.setattr("gerber2rml.app.presets._user_path",
                        lambda: tmp_path / "presets.json")
    st = ProjectState()
    st.trace.bit_diameter = 0.6
    save_user_preset("mine", st)
    presets = load_presets()
    assert "mine" in presets
    assert presets["mine"]["trace"]["bit_diameter"] == 0.6


def test_a_ball_mill_written_the_old_way_becomes_a_ball():
    """Until 0.22.1.3 the ball profile's trace bit was "0.44 mm flat" beside
    a 0.8 mm drill: two bits to the run plan, which refused every automatic
    run. Saved profiles and setups still say it that way."""
    from gerber2rml.app.presets import jobs_of
    from gerber2rml.engine import method as fab
    old = {"trace": {"bit_diameter": 0.44, "cut_depth": 0.08,
                     "cut_pinches": True},
           "drill": {"bit_diameter": 0.8}, "cutout": {"bit_diameter": 0.8}}
    trace, drill, _cutout = jobs_of(old)
    assert (trace.tool_type, trace.bit_diameter) == ("ball", 0.8)
    assert abs(trace.effective_diameter() - 0.44) < 1e-9
    assert fab.of_trace(trace) == fab.BALL
    # A real fine flat endmill is left alone.
    fine = jobs_of({"trace": {"bit_diameter": 0.4},
                    "drill": {"bit_diameter": 0.8}})[0]
    assert (fine.tool_type, fine.bit_diameter) == ("flat", 0.4)


def test_a_ball_cut_deep_is_still_the_ball_method():
    from gerber2rml.config import TraceJob
    from gerber2rml.engine import method as fab
    deep = TraceJob(tool_type="ball", bit_diameter=0.8, cut_depth=0.25)
    assert deep.effective_diameter() > 0.6      # wider than "under 0.6 mm"
    assert fab.of_trace(deep) == fab.BALL
