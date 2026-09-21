import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QStandardPaths
from parkloop.app import Window
from parkloop.core import Route,read_gpx,write_gpx
from parkloop.mapview import MapView


def test_auto_route_is_the_clear_open_default(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(Window,'restore',lambda self:None)
    monkeypatch.setattr(Window,'persist',lambda self:None)
    monkeypatch.setattr(MapView,'load_tile',lambda *args:None)
    w=Window()
    assert w.mode.currentIndex()==1
    assert w.mode.currentText()=='Auto route · create a loop'
    assert w.preference.currentText()=='Park-first loop · maximize green paths'
    assert w.start_toggle.isChecked()
    assert w.location.isVisibleTo(w)
    assert w.generate.text()=='3. Find route options'
    assert w.preference_label.isVisibleTo(w)
    assert not w.snap.isVisibleTo(w)
    w.close()


def test_named_exports_and_last_five_files(tmp_path,monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Window,'restore',lambda self:None)
    monkeypatch.setattr(Window,'persist',lambda self:None)
    monkeypatch.setattr(MapView,'load_tile',lambda *a:None)
    settings=QSettings('ParkLoop','ParkLoop')
    before={key:settings.value(key) for key in ('export/folder','export/history')}
    try:
        settings.setValue('export/folder',str(tmp_path/'gpx'));settings.remove('export/history')
        w=Window();w.route=Route(segments=[[(32,34),(32.001,34.001)]])
        for i in range(6):
            w.name.setText(f'Run {i}');w.save_gpx()
        assert (tmp_path/'gpx'/'Run 5.gpx').exists()
        assert w.recent_exports.count()==5
        assert w.recent_exports.item(0).text()=='Run 5.gpx'
        assert w.recent_exports.item(4).text()=='Run 1.gpx'
        assert read_gpx(tmp_path/'gpx'/'Run 5.gpx').name=='Run 5'
        monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.No)
        w.name.setText('Run 1');w.save_gpx()
        assert w.recent_exports.item(0).text()=='Run 5.gpx'
        monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
        w.save_gpx()
        assert w.recent_exports.item(0).text()=='Run 1.gpx'
        assert w.recent_exports.count()==5
        w.close();w=Window()
        assert w.recent_exports.item(0).text()=='Run 1.gpx'
        assert Window.gpx_filename('../Morning/run')=='_Morning_run.gpx'
        w.close()
    finally:
        for key,value in before.items():
            if value is None:settings.remove(key)
            else:settings.setValue(key,value)


def test_manual_edit_undo_redo_and_export(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    app.setApplicationName('ParkLoopTests');QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setattr(Window,'restore',lambda self:None)
    monkeypatch.setattr(Window,'persist',lambda self:None)
    monkeypatch.setattr(MapView,'load_tile',lambda *args:None)
    w=Window();w.mode.setCurrentIndex(0);w.snap.setCurrentIndex(1)
    initial_start=w.start
    w.map_click(32,34)
    assert w.start==initial_start and not w.route.geometry
    w.pick_start.setChecked(True);w.map_click(32,34)
    assert not w.pick_start.isChecked()
    w.map_click(32.01,34.01)
    assert w.start==(32,34)
    assert len(w.route.geometry)==2 and w.route.distance_m>1000
    w.move_point(0,1,32.02,34.02)
    w.undo_route();assert w.route.geometry[-1]==(32.01,34.01)
    w.redo_route();assert w.route.geometry[-1]==(32.02,34.02)
    w.insert_point(0,1,32.005,34.005);assert len(w.route.geometry)==3
    w.delete_point(0,1);assert len(w.route.geometry)==2
    w.close_route();assert w.route.geometry[0]==w.route.geometry[-1]
    path=tmp_path/'edited.gpx';write_gpx(w.route,path)
    assert read_gpx(path).geometry==w.route.geometry
    w.mode.setCurrentIndex(1)
    previous=w.start;w.map_click(32.1,34.1);assert w.start==previous
    w.pick_start.setChecked(True);w.map_click(32.1,34.1)
    assert w.start==(32.1,34.1) and not w.map.edit
    assert not w.pick_start.isChecked()
    w.map_click(32.2,34.2);assert w.start==(32.1,34.1)
    w.set_busy(True);before=w.route.geometry[:];w.new_route();assert w.route.geometry==before
    w.set_busy(False)
    w.commit(Route('Elevations', [[(32,34),(32.01,34.01),(32.02,34.02)]], [[12,13,14]]))
    w.move_point(0,1,32.011,34.011)
    assert w.route.elevations==[[12,None,14]]
    w.delete_point(0,1);assert w.route.elevations==[[12,14]]
    w.close()


def test_road_check_status_clears_after_edit(tmp_path,monkeypatch):
    import time
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Window,'restore',lambda self:None)
    monkeypatch.setattr(Window,'persist',lambda self:None)
    monkeypatch.setattr(MapView,'load_tile',lambda *args:None)
    elements=[{'type':'way','id':1,'nodes':[1,2],
               'geometry':[{'lat':32,'lon':34},{'lat':32.001,'lon':34.001}],
               'tags':{'highway':'footway'}}]
    monkeypatch.setattr('parkloop.app.fetch_elements',lambda *a:elements)
    w=Window();w.store=tmp_path/'draft.json'
    w.commit(Route('Check', [[(32,34),(32.001,34.001)]]))
    w.check_road_safety()
    deadline=time.monotonic()+2
    while w.busy and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert not w.busy and 'Map check passed' in w.safety_note.text()
    assert (tmp_path/'last-road-safety-check.json').exists()
    w.snap.setCurrentIndex(1)
    w.move_point(0,1,32.002,34.002)
    assert 'not checked' in w.safety_note.text()
    w.check_road_safety()
    deadline=time.monotonic()+2
    while w.busy and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert not w.busy and 'Not verified' in w.safety_note.text()
    w.close()


def test_alternative_selection_keeps_unverified_statistics(monkeypatch):
    from parkloop.alternatives import Alternative
    from parkloop.parks import ParkResult
    from parkloop.safety import SafetyAudit
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Window,'restore',lambda self:None)
    monkeypatch.setattr(Window,'persist',lambda self:None)
    monkeypatch.setattr(MapView,'load_tile',lambda *args:None)
    w=Window()
    route=Route('Alternative',[[(32,34),(32.001,34),(32.001,34.001),(32,34)]])
    distance=route.distance_m
    alternative=Alternative(ParkResult(route,.8,12,0),SafetyAudit(checked_m=distance*.7,unknown_m=distance*.3),distance)
    w.show_alternatives([alternative])
    # Preview appears in the side panel without needing a modal.
    assert w.alt_list.isVisibleTo(w)
    assert w.route.name=='Alternative'
    assert '30%' in w.details.text()
    assert 'without map verification' in w.safety_note.text()
    w.use_selected_alternative()
    assert w.route.name=='Alternative'
    assert 'without map verification' in w.safety_note.text()
    w.dismiss_alternatives()
    assert not w.alt_list.isVisible()
    w.close()


def test_alternative_switch_previews_each_route(monkeypatch):
    from parkloop.alternatives import Alternative
    from parkloop.parks import ParkResult
    from parkloop.safety import SafetyAudit
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Window,'restore',lambda self:None)
    monkeypatch.setattr(Window,'persist',lambda self:None)
    monkeypatch.setattr(MapView,'load_tile',lambda *args:None)
    w=Window()
    w.commit(Route('Original', [[(32,34),(32.001,34.001)]]))
    assert not w.alt_list.isVisible()
    r1=Route('Alt one',[[(32,34),(32.002,34),(32.002,34.002),(32,34)]])
    r2=Route('Alt two',[[(32,34),(32.003,34),(32.003,34.003),(32,34)]])
    a1=Alternative(ParkResult(r1,.9,5,0),SafetyAudit(checked_m=r1.distance_m),r1.distance_m)
    a2=Alternative(ParkResult(r2,.5,50,0),SafetyAudit(checked_m=r2.distance_m*.5,unknown_m=r2.distance_m*.5),r2.distance_m)
    w.show_alternatives([a1,a2])
    assert w.alt_list.count()==2
    assert w.route.name=='Alt one'
    # Switch selection previews the second route on the map.
    w.alt_list.setCurrentRow(1)
    assert w.route.name=='Alt two'
    assert w.map.route.name=='Alt two'
    assert '50%' in w.details.text()
    # Switching back previews the first again.
    w.alt_list.setCurrentRow(0)
    assert w.route.name=='Alt one'
    # Dismiss without Use restores the pre-generation route.
    w.dismiss_alternatives()
    assert w.route.name=='Original'
    # Preview + Use commits with undo history.
    w.show_alternatives([a1,a2])
    w.alt_list.setCurrentRow(1)
    history_len=len(w.history)
    w.use_selected_alternative()
    assert w.route.name=='Alt two'
    assert len(w.history)==history_len+1
    # Panel stays open so the user can try another option.
    assert w.alt_list.isVisibleTo(w)
    w.close()
