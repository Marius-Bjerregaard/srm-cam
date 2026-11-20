"""The field test on the laser page (engine/laser_fieldtest.py): loaded as a
traces job to place on a spare sheet like a board, and cut with the page's
recipe - each ladder height as its own band, the head moved for it."""
from gerber2rml.engine import laser_fieldtest as ft


def load(page):
    """Put the field test on the page in place of the job."""
    art, heights = ft.field_test_art()
    if page.show_job(art, "Field test") is False:
        return                              # the laser is busy: said why
    page._field_heights = heights
    page._say("Field test loaded: drag it onto a spare sheet (160 x 100), "
              "Autofocus, then Etch. " + ft.key())


def active(page):
    """The page's job is the field test, with the heights to cut it at."""
    art = getattr(page, "_source", None)       # the job as it was given
    return (art is not None and getattr(art, "note", None) == ft.NOTE
            and getattr(page, "_field_heights", None) is not None)


def heights(page):
    """The height to cut each of ``page.art.apertures`` at, trench for
    trench, found by where it lies.

    ``ft.field_test_art`` gives a height per copper shape (78: a pad and a
    trace per coupon), but the page shows the isolation round them, rebuilt
    from the copper for its recipe (39 regions, one round each coupon). Read
    by number, 24 coupons were cut at another coupon's height and the bands
    past 38 etched the whole sheet (review, 2026-11-20). So each trench takes
    the height of the copper it isolates, with the page's mirror and flip."""
    from shapely.strtree import STRtree
    from gerber2rml.engine import xtool
    shapes = list(xtool.reoriented(page._source, page._extra_mirror,
                                   page._extra_flip).apertures)
    hs = page._field_heights
    if len(shapes) != len(hs):
        raise ValueError("The field test's heights are not its shapes': "
                         "load the field test again.")
    tree = STRtree(shapes)
    out = []
    for region in page.base_art.apertures:
        near = sorted({round(hs[int(i)], 3) for i in
                       tree.query(region, predicate="dwithin", distance=0.05)})
        if not near:
            near = [round(hs[int(tree.nearest(region))], 3)]
        if len(near) > 1:
            raise ValueError(
                f"The field test's isolation joins coupons cut at different "
                f"heights ({', '.join(f'{d:+.1f}' for d in near)} mm): use "
                f"concentric rings or a moat, not a full rubout, and fewer "
                f"rings.")
        out.append(near[0])
    return out


def zones(page, only=None):
    """``[(dz, {aperture index})]`` for the run."""
    return ft.groups(heights(page), only)
