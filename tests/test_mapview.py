import math
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPointF, QStandardPaths
from PySide6.QtWidgets import QApplication
from parkloop.app import Window
from parkloop.core import Route
from parkloop.mapview import MapView, _map_colors, arrow_placements


def _app():
    return QApplication.instance() or QApplication([])


def test_arrow_placements_follow_direction_and_spacing():
    east = arrow_placements([QPointF(0, 0), QPointF(250, 0)])
    assert len(east) == 2
    assert east[0][0].x() == 110.0 and east[1][0].x() == 220.0
    assert all(abs(angle) < 1e-9 for _, angle in east)
    north = arrow_placements([QPointF(0, 200), QPointF(0, 0)])
    assert north and abs(north[0][1] + math.pi / 2) < 0.01
    assert arrow_placements([QPointF(0, 0), QPointF(10, 0)]) == []
    assert arrow_placements([QPointF(5, 5)]) == []


def test_map_palettes_differ_between_modes():
    dark, light = _map_colors('dark'), _map_colors('light')
    assert dark['background'] != light['background']
    assert dark['tile_dim'] > 0 and light['tile_dim'] == 0
    assert dark['route'] == light['route']


def test_map_mode_defaults_to_light_and_validation(monkeypatch):
    _app()
    monkeypatch.setattr(MapView, 'load_tile', lambda *args: None)
    view = MapView()
    assert view.map_mode == 'light'
    assert view.set_map_mode('light') == 'light'
    try:
        view.set_map_mode('sepia')
    except ValueError:
        pass
    else:
        raise AssertionError('invalid map mode should raise')


def test_route_paints_with_arrows(monkeypatch):
    app = _app()
    monkeypatch.setattr(MapView, 'load_tile', lambda *args: None)
    view = MapView()
    assert view.map_mode == 'light'
    view.resize(600, 500)
    view.route = Route('t', [[(32.10, 34.81), (32.11, 34.82), (32.12, 34.81)]])
    view.fit(view.route.geometry)
    app.processEvents()
    assert not view.grab().isNull()


def test_window_map_defaults_to_light(monkeypatch):
    _app()
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setattr(Window, 'restore', lambda self: None)
    monkeypatch.setattr(Window, 'persist', lambda self: None)
    monkeypatch.setattr(MapView, 'load_tile', lambda *args: None)
    w = Window()
    assert w.map.map_mode == 'light'
    assert not hasattr(w, 'map_toggle')
    assert not hasattr(w, 'dark_map_action')
    w.close()


def test_drag_preview_moves_without_changing_route(monkeypatch):
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    app=_app()
    monkeypatch.setattr(MapView,'load_tile',lambda *args:None)
    view=MapView(); view.resize(600,500)
    points=[view.center,(32.102,34.813)]
    view.route=Route(segments=[points.copy()]); view.show(); app.processEvents()
    moved=[]; view.moved.connect(lambda *args:moved.append(args))
    start=view.screen(points[0]).toPoint(); end=start+QPoint(50,30)
    QTest.mousePress(view,Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(view,end)
    assert view.drag_position==QPointF(end)
    assert view.route.geometry==points and moved==[]
    assert not view.grab().isNull()
    QTest.mouseRelease(view,Qt.MouseButton.LeftButton,pos=end)
    assert len(moved)==1 and moved[0][:2]==(0,0)
    assert view.drag_position is None
    assert view.route.geometry==points
    view.close()


def test_multi_select_and_delete_signal(monkeypatch):
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    app=_app(); monkeypatch.setattr(MapView,'load_tile',lambda *a:None)
    view=MapView(); view.resize(600,500); view.route=Route(segments=[[(32.1,34.81),(32.101,34.811),(32.102,34.812)]])
    view.fit(view.route.geometry);view.show();app.processEvents();deleted=[];view.deleted_many.connect(lambda p:deleted.append(p))
    for point in view.route.geometry[:2]:
        QTest.keyClick(view,Qt.Key.Key_Control)
        QTest.mouseClick(view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,pos=view.screen(point).toPoint())
    assert deleted==[] and view.selected=={(0,0),(0,1)}
    QTest.keyClick(view,Qt.Key.Key_Delete)
    assert deleted==[{(0,0),(0,1)}] and not view.selected
    view.close()


def test_explicit_selection_mode_does_not_need_keyboard_modifier(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app=_app(); monkeypatch.setattr(MapView,'load_tile',lambda *a:None)
    view=MapView(); view.resize(600,500); view.route=Route(segments=[[(32.1,34.81),(32.101,34.811)]])
    view.fit(view.route.geometry);view.select_mode=True;view.show();app.processEvents()
    QTest.mouseClick(view,Qt.MouseButton.LeftButton,pos=view.screen(view.route.geometry[0]).toPoint())
    assert view.selected=={(0,0)}
    view.close()
