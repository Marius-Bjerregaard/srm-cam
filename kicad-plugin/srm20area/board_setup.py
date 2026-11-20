"""Open KiCad's own Board Setup, so the rules just set are the ones used.

KiCad draws and checks with rules it compiles from the board's settings,
and it compiles them again when Board Setup is closed with OK, or the board
is opened - not when a plugin changes the settings. So after *Set design
rules for SRM-CAM* the net class read 1 mm in Board Setup while a new track
came out 0.2 mm, "from netclass 'Default'" (2026-11-20). There is no call
for that in KiCad's scripting API; the menu item is the way, as a person
would take it.
"""


def is_board_setup(label):
    """Is a menu label KiCad's "Board Setup..."? Accelerators, ellipses and
    case aside."""
    text = (label or "").replace("&", "").replace("…", "")
    text = text.split("\t")[0].rstrip(". ").strip().lower()
    return text == "board setup"


def _items(menu):
    for item in menu.GetMenuItems():
        sub = item.GetSubMenu()
        if sub is not None:
            for found in _items(sub):
                yield found
        else:
            yield menu, item


def _editor_frame():
    import wx
    frame = wx.FindWindowByName("PcbFrame")
    if frame is not None:
        return frame
    for win in wx.GetTopLevelWindows():
        if "PCB Editor" in (win.GetTitle() or ""):
            return win
    return None


def find_menu_item():
    """``(menu, item)`` for Board Setup in the PCB editor, or None."""
    frame = _editor_frame()
    bar = frame.GetMenuBar() if frame is not None else None
    if bar is None:
        return None
    for i in range(bar.GetMenuCount()):
        for menu, item in _items(bar.GetMenu(i)):
            if is_board_setup(item.GetItemLabelText()):
                return menu, item
    return None


def open_later():
    """Open Board Setup once the plugin has returned. False when this KiCad
    has no such menu item to take (then the operator is told to)."""
    found = find_menu_item()
    if found is None:
        return False
    menu, item = found
    import wx

    def go():
        # KiCad's menus handle their own events (ACTION_MENU), so the event
        # goes to the menu, as a click on it would.
        evt = wx.CommandEvent(wx.wxEVT_MENU, item.GetId())
        evt.SetEventObject(menu)
        menu.ProcessEvent(evt)
    wx.CallAfter(go)
    return True
