import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QApplication
from parkloop.app import Window
from parkloop.mapview import MapView


def _make_window(monkeypatch, recent=None, labels=None):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('ParkLoopTests')
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setattr(Window, 'restore', lambda self: None)
    monkeypatch.setattr(Window, 'persist', lambda self: None)
    monkeypatch.setattr(MapView, 'load_tile', lambda *args: None)
    if recent is not None:
        monkeypatch.setattr(Window, '_load_recent_starts', lambda self: list(recent))
        monkeypatch.setattr(Window, '_save_recent_starts', lambda self: None)
    if labels is not None:
        monkeypatch.setattr(Window, '_load_place_labels', lambda self: dict(labels))
    w = Window()
    return w


def test_remember_caps_at_ten_most_recent_first(monkeypatch):
    w = _make_window(monkeypatch, recent=[])
    for i in range(12):
        w.remember_start((32.0 + i * 0.01, 34.0))
    assert len(w.recent_starts) == 10
    assert w.recent_starts[0] == (round(32.0 + 11 * 0.01, 6), 34.0)
    assert (32.0, 34.0) not in w.recent_starts
    assert w.recent_combo.count() == 10
    w.close()


def test_remember_dedupes_to_front(monkeypatch):
    w = _make_window(monkeypatch, recent=[(32.1, 34.1), (32.2, 34.2), (32.3, 34.3)])
    w.remember_start((32.2, 34.2))
    assert w.recent_starts == [(32.2, 34.2), (32.1, 34.1), (32.3, 34.3)]
    assert w.recent_combo.count() == 3
    w.close()


def test_set_location_records_history(monkeypatch):
    w = _make_window(monkeypatch, recent=[])
    w.location.setText('32.5, 34.9')
    w.set_location()
    assert w.start == (32.5, 34.9)
    assert w.recent_starts[0] == (32.5, 34.9)
    assert w.recent_combo.itemData(0) == (32.5, 34.9)
    w.close()


def test_combo_choice_sets_start(monkeypatch):
    w = _make_window(monkeypatch, recent=[(32.1, 34.1), (32.2, 34.2)])
    w.recent_combo.setCurrentIndex(1)
    assert w.start == (32.2, 34.2)
    assert w.location.text() == '32.200000, 34.200000'
    assert w.map.center == (32.2, 34.2)
    assert w.recent_starts[0] == (32.2, 34.2)
    w.close()


def test_labeled_places_stay_first_and_are_not_readded(monkeypatch):
    home=(32.1,34.1); park=(32.3,34.3); recent=(32.2,34.2)
    labels={'32.100000, 34.100000':'Home','32.300000, 34.300000':'Park'}
    w=_make_window(monkeypatch,recent=[recent,home,park],labels=labels)
    assert w.recent_starts==[home,park,recent]
    assert [w.recent_combo.itemText(i) for i in range(3)]==['Home','Park','32.200000, 34.200000']

    # Entering or choosing Home again neither duplicates it nor changes the
    # stable order of pinned places.
    w.location.setText('32.1, 34.1'); w.set_location()
    assert w.recent_starts==[home,park,recent]
    w.recent_combo.setCurrentIndex(1)
    assert w.recent_starts==[home,park,recent]
    assert w.recent_starts.count(home)==1

    # New unlabeled history is still ordered by recency, below saved places.
    w.remember_start((32.4,34.4))
    assert w.recent_starts==[home,park,(32.4,34.4),recent]
    w.close()


def test_recent_combo_disabled_while_busy(monkeypatch):
    w = _make_window(monkeypatch, recent=[(32.1, 34.1)])
    w.set_busy(True)
    assert not w.recent_combo.isEnabled()
    w.set_busy(False)
    assert w.recent_combo.isEnabled()
    w.close()


def test_changing_start_updates_existing_route_and_undo(monkeypatch):
    from parkloop.core import Route
    w=_make_window(monkeypatch,recent=[(32.2,34.2)])
    w.mode.setCurrentIndex(0)
    original=[(32.1,34.1),(32.11,34.11)]
    w.commit(Route(segments=[original.copy()]))
    requests=[]
    def calculate(points):
        requests.append(points)
        return Route(segments=[points])
    monkeypatch.setattr(w.router,'calculate_route',calculate)
    monkeypatch.setattr(w,'job',lambda work,callback:callback(work()))
    w.location.setText('32.15, 34.15'); w.set_location()
    assert requests[-1]==[(32.15,34.15),original[1]]
    assert w.start==w.route.geometry[0]==(32.15,34.15)
    w.undo_route()
    assert w.start==w.route.geometry[0]==original[0]
    w.redo_route()
    assert w.start==w.route.geometry[0]==(32.15,34.15)
    index=w.recent_starts.index((32.2,34.2))
    w.recent_combo.setCurrentIndex(index)
    assert w.start==w.route.geometry[0]==(32.2,34.2)
    w.close()


def test_history_persists_across_windows(monkeypatch):
    settings = QSettings('ParkLoop', 'ParkLoop')
    before = settings.value('start/history')
    try:
        w1 = _make_window(monkeypatch, recent=None)
        w1.recent_starts = []
        w1.remember_start((33.123456, 35.123456))
        w1.close()
        w2 = _make_window(monkeypatch, recent=None)
        assert w2.recent_starts[0] == (33.123456, 35.123456)
        assert w2.recent_combo.count() >= 1
        w2.close()
    finally:
        if before is None:
            settings.remove('start/history')
        else:
            settings.setValue('start/history', before)


def test_optional_labels_persist_follow_places_and_can_be_cleared(monkeypatch):
    settings=QSettings('ParkLoop','ParkLoop')
    before=settings.value('start/labels')
    try:
        settings.remove('start/labels')
        w=_make_window(monkeypatch,recent=[(32.1,34.1),(32.2,34.2)])
        w.recent_combo.setCurrentIndex(0)
        w.place_label.setText('  Home  '); w.save_recent_label()
        assert w.recent_combo.currentText()=='Home'
        assert w.recent_combo.currentData()==(32.1,34.1)
        w.recent_combo.setCurrentIndex(1)
        assert w.place_label.text()==''
        w.place_label.setText('Park'); w.save_recent_label()
        w.recent_combo.setCurrentIndex(1)
        assert w.place_label.text()=='Home'
        w.set_busy(True)
        assert not w.save_place_label.isEnabled()
        w.set_busy(False); w.close()
        w=_make_window(monkeypatch,recent=[(32.1,34.1),(32.2,34.2)])
        w.recent_combo.setCurrentIndex(0)
        assert w.place_label.text()=='Home'
        w.place_label.clear(); w.save_recent_label()
        assert w.recent_combo.currentText()=='32.100000, 34.100000'
        assert w.recent_combo.itemText(0)=='Park'
        w.close()
    finally:
        if before is None:settings.remove('start/labels')
        else:settings.setValue('start/labels',before)
