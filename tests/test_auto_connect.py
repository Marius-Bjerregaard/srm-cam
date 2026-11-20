"""The machine bar connects to the Arduino by itself - when there is exactly
one, nothing has the machine, and nobody said not to."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from gerber2rml.gui2 import machine

_app = QApplication.instance() or QApplication([])

UNO = ("/dev/cu.usbserial-1140", "CH340 (Uno clone)")
UNO2 = ("/dev/cu.usbserial-2210", "CH340 (Uno clone)")
OTHER = ("/dev/cu.debug-console", "unknown device")


@pytest.fixture
def bar(monkeypatch):
    ports = {"now": []}
    monkeypatch.setattr(machine, "list_ports",
                        lambda sim_only=False: (list(ports["now"]), ""))
    link = machine.MachineLink()
    tried = []
    monkeypatch.setattr(link, "connect_to", lambda port: tried.append(port))
    b = machine.MachineBar(link)
    b._auto_in_tests = True
    from gerber2rml.gui2 import workspace
    workspace.set_setting(b.AUTO_KEY, "1")
    b.ports, b.tried = ports, tried
    yield b
    b.deleteLater()


def _plug(bar, *ports):
    bar.ports["now"] = list(ports)
    bar._connecting_until = 0.0
    bar._auto_rescan()


def test_one_arduino_plugged_in_is_connected_by_itself(bar):
    _plug(bar, OTHER, UNO)
    assert bar.tried == [UNO[0]]
    assert bar.current_port() == UNO[0]


def test_two_arduinos_are_left_to_the_person(bar):
    _plug(bar, UNO, UNO2)
    assert bar.tried == []


def test_an_unknown_device_is_never_connected_by_itself(bar):
    _plug(bar, OTHER)
    assert bar.tried == []


def test_after_disconnect_it_waits_until_the_board_is_plugged_in_again(bar):
    bar._user_off = True                       # Disconnect was pressed
    _plug(bar, UNO)
    assert bar.tried == []
    _plug(bar)                                 # unplugged...
    _plug(bar, UNO)                            # ...and back
    assert bar.tried == [UNO[0]]


def test_a_failed_connect_is_not_tried_every_two_seconds(bar):
    _plug(bar, UNO)
    bar._on_failed("connect", "SerialException: busy")
    bar.link._ser = None
    _plug(bar)
    _plug(bar, OTHER)
    assert bar.tried == [UNO[0]]               # the port went: forgotten
    _plug(bar, OTHER, UNO)
    assert bar.tried == [UNO[0], UNO[0]]       # plugged in anew: tried again


def test_not_while_a_run_holds_the_machine(bar):
    bar._held = True
    _plug(bar, UNO)
    assert bar.tried == []


def test_switched_off_in_the_machine_menu(bar):
    bar.set_auto_connect(False)
    _plug(bar, UNO)
    assert bar.tried == []
