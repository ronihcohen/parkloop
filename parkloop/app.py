"""Cross-platform native desktop application."""
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
import platform
import re
from pathlib import Path
import sys
import threading
import traceback
from PySide6.QtCore import QObject, QSettings, Signal, QStandardPaths, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
    QVBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QDoubleSpinBox,
    QFileDialog, QMessageBox, QProgressBar, QFrame, QScrollArea, QListWidget, QListWidgetItem,
    QSplitter)
from .core import Route, RoutingError, FootRouter, read_gpx, write_gpx, generate_any, validate_coords
from .parks import generate_park_route, fetch_elements
from .safety import audit_route
from .alternatives import generate_alternatives
from .mapview import MapView
from .theme import build_stylesheet, load_omarchy_colors
from . import mapdata


def auto_route_log_path():
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / 'last-auto-route.log'


def write_auto_route_log(message, *, reset=False):
    """Write diagnostics without allowing a logging failure to break routing."""
    try:
        path=auto_route_log_path()
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w' if reset else 'a',encoding='utf-8') as stream:
            stream.write(message.rstrip()+'\n')
        return path
    except OSError:
        return None


class Events(QObject):
    complete = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class Window(QMainWindow):
    MAX_RECENT_STARTS = 10

    def __init__(self):
        super().__init__()
        self.setWindowTitle('ParkLoop — GPX editor')
        self.resize(1260, 840)
        self.route = Route()
        self.history, self.future = [], []
        self.start = (32.1044, 34.8103)
        self.recent_starts = self._load_recent_starts()
        self.place_labels = self._load_place_labels()
        self._pin_labeled_starts()
        self._label_point = None
        self._syncing_recent = False
        self.busy = False
        self.cancel = threading.Event()
        self.router = FootRouter(include_unverified=True)
        mapdata.offline_path = QSettings('ParkLoop','ParkLoop').value('maps/offline_path','') or None
        self.events = Events()
        self.events.complete.connect(self.completed)
        self.events.failed.connect(self.failed)
        self.events.progress.connect(lambda s: self.status.setText(s))
        root = QWidget(); layout = QHBoxLayout(root); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        self.setCentralWidget(root)
        sidebar = QFrame(); sidebar.setObjectName('sidebar')
        box = QVBoxLayout(sidebar); box.setContentsMargins(20,20,20,16); box.setSpacing(10)
        brand = QLabel('◉  ParkLoop'); brand.setObjectName('brand'); box.addWidget(brand)
        sub = QLabel('Plan your next run'); sub.setObjectName('muted'); box.addWidget(sub)
        box.addWidget(QLabel('Planning mode'))
        self.mode = QComboBox(); self.mode.addItems(['Manual editor · draw or edit', 'Auto route · create a loop']); self.mode.setCurrentIndex(1); box.addWidget(self.mode)
        box.addWidget(QLabel('Route name'))
        self.name = QLineEdit('Untitled run'); box.addWidget(self.name)
        self.start_toggle, start_box = self.disclosure(box,'1. Choose starting point')
        self.start_toggle.setChecked(True)
        self.pick_start=QPushButton('Choose start on map'); self.pick_start.setCheckable(True); start_box.addWidget(self.pick_start)
        self.pick_start.toggled.connect(self.toggle_start_picker)
        start_box.addWidget(QLabel('Or enter latitude, longitude'))
        self.location = QLineEdit('32.104400, 34.810300'); start_box.addWidget(self.location)
        self.location.setPlaceholderText('Example: 32.104400, 34.810300')
        self.locate = QPushButton('Use these coordinates'); start_box.addWidget(self.locate)
        self.locate.clicked.connect(self.set_location)
        start_box.addWidget(QLabel('Saved and recent places'))
        self.recent_combo = QComboBox(); self.recent_combo.setToolTip('Pick a recent starting point to use it again'); start_box.addWidget(self.recent_combo)
        self.recent_combo.currentIndexChanged.connect(self._recent_start_chosen)
        self._refresh_recent_combo()
        label_row=QHBoxLayout()
        self.place_label=QLineEdit(); self.place_label.setPlaceholderText('Optional label, e.g. Home'); self.place_label.setMaxLength(80)
        self.save_place_label=QPushButton('Save label')
        label_row.addWidget(self.place_label,1); label_row.addWidget(self.save_place_label); start_box.addLayout(label_row)
        self.save_place_label.clicked.connect(self.save_recent_label)
        self.place_label.returnPressed.connect(self.save_recent_label)
        self._sync_place_label()
        self.hint = QLabel('Click to add points. Drag a point to move it.\nCtrl/Cmd-click points to select, Ctrl/Cmd-drag a rectangle for a group (Shift-drag adds).\nDelete removes the selection · Esc clears it · right-click deletes. Shift-click a line to insert.'); self.hint.setWordWrap(True); box.addWidget(self.hint)
        self.segment_label=QLabel('Routing preference'); box.addWidget(self.segment_label)
        self.snap = QComboBox(); self.snap.addItems(['Shortest walking route', 'Straight lines', 'Verified walking route']); box.addWidget(self.snap)
        self.snap.setToolTip('Choose the shortest allowed walking route, or require verified paths and sidewalks. Applies to new sections and point edits.')
        self.snap.currentIndexChanged.connect(lambda index: setattr(self.router,'include_unverified',index==0))
        self.routing_note=QLabel(); self.routing_note.setObjectName('muted'); self.routing_note.setWordWrap(True); box.addWidget(self.routing_note)
        self.snap.currentIndexChanged.connect(self.update_routing_note)
        self.update_routing_note()
        self.distance = QDoubleSpinBox(); self.distance.setRange(1,30); self.distance.setValue(10); self.distance.setSuffix(' km'); self.distance.setSingleStep(.5)
        self.target_label=QLabel('2. Target distance'); box.addWidget(self.target_label); box.addWidget(self.distance)
        self.preference_label=QLabel('Route preference'); box.addWidget(self.preference_label)
        self.preference = QComboBox(); self.preference.addItems(['Park-first loop · maximize green paths', 'Entirely inside green zones', 'Any walkable route']); box.addWidget(self.preference)
        self.generate = QPushButton('3. Find route options'); self.generate.setObjectName('primary'); box.addWidget(self.generate); self.generate.clicked.connect(self.auto)
        self.alt_title = QLabel('Alternatives · select to preview'); self.alt_title.setWordWrap(True); box.addWidget(self.alt_title)
        self.alt_list = QListWidget(); self.alt_list.setMaximumHeight(180); self.alt_list.setWordWrap(True); self.alt_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); box.addWidget(self.alt_list)
        self.alt_list.currentRowChanged.connect(self.preview_alternative)
        self.alt_details = QLabel(); self.alt_details.setWordWrap(True); box.addWidget(self.alt_details)
        alt_row = QHBoxLayout()
        self.use_alt = QPushButton('Use this route'); self.use_alt.setObjectName('primary'); alt_row.addWidget(self.use_alt)
        self.dismiss_alt = QPushButton('Dismiss'); alt_row.addWidget(self.dismiss_alt); box.addLayout(alt_row)
        self.use_alt.clicked.connect(self.use_selected_alternative); self.dismiss_alt.clicked.connect(self.dismiss_alternatives)
        self.alternatives = []; self.pre_alt_route = None; self._applying_alt = False; self._previewing = False
        for widget in (self.alt_title, self.alt_list, self.alt_details, self.use_alt, self.dismiss_alt):
            widget.hide()
        row = QHBoxLayout()
        self.close_loop = QPushButton('Close loop'); row.addWidget(self.close_loop); self.close_loop.clicked.connect(self.close_route)
        self.select_points = QPushButton('Select points'); self.select_points.setCheckable(True); row.addWidget(self.select_points); self.select_points.toggled.connect(self.toggle_point_selection)
        self.delete_selected = QPushButton('Delete selected'); self.delete_selected.setEnabled(False); row.addWidget(self.delete_selected); self.delete_selected.clicked.connect(lambda _checked=False: self.delete_selected_points())
        self.undo = QPushButton('Undo'); self.redo = QPushButton('Redo'); row.addWidget(self.undo); row.addWidget(self.redo); box.addLayout(row)
        self.undo.clicked.connect(self.undo_route); self.redo.clicked.connect(self.redo_route)
        self.details_toggle, details_box = self.disclosure(box,'Route details')
        self.details = QLabel('Your next run starts here.'); self.details.setWordWrap(True); details_box.addWidget(self.details)
        self.help_toggle, help_box = self.disclosure(box,'Map controls')
        help_text=QLabel('Click to add · drag a point to move\nCtrl/Cmd-drag a rectangle to select a group (Shift adds)\nRight-click a point to delete · right-click a selection deletes the group\nDelete removes selection · Esc clears · Shift-click a line inserts\nDrag the map to pan · scroll to zoom')
        help_text.setWordWrap(True); help_box.addWidget(help_text)
        self.safety_note = QLabel('Road safety: not checked.'); self.safety_note.setWordWrap(True); box.addWidget(self.safety_note)
        self.check_button = QPushButton('Check road safety'); self.check_button.clicked.connect(self.check_road_safety); box.addWidget(self.check_button)
        box.addWidget(QLabel('Recent exports'))
        self.recent_exports=QListWidget(); self.recent_exports.setMaximumHeight(150)
        self.recent_exports.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        box.addWidget(self.recent_exports); self.refresh_recent_exports()
        box.addStretch()
        footer=QFrame(); footer.setObjectName('sidebarFooter')
        footer_box=QVBoxLayout(footer); footer_box.setContentsMargins(20,14,20,18); footer_box.setSpacing(8)
        self.stats = QLabel('0.00 km'); self.stats.setObjectName('distance'); footer_box.addWidget(self.stats)
        box=footer_box
        self.status = QLabel('Ready. Choose a starting point, set the distance, then find route options.'); self.status.setWordWrap(True); box.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0,0); self.progress.hide(); box.addWidget(self.progress)
        self.cancel_button = QPushButton('Cancel'); self.cancel_button.hide(); self.cancel_button.clicked.connect(self.cancel_job); box.addWidget(self.cancel_button)
        self.export = QPushButton('Export GPX'); self.export.setObjectName('primary'); self.export.clicked.connect(self.save_gpx); box.addWidget(self.export)
        self.map = MapView(); scroll=QScrollArea(); scroll.setWidget(sidebar); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel=QFrame(); panel.setObjectName('sidebar'); panel.setMinimumWidth(240)
        panel_box=QVBoxLayout(panel); panel_box.setContentsMargins(0,0,0,0); panel_box.setSpacing(0); panel_box.addWidget(scroll,1); panel_box.addWidget(footer)
        # Draggable divider so the user can resize the side panel vs the map.
        self.splitter=QSplitter(Qt.Orientation.Horizontal); self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(panel); self.splitter.addWidget(self.map)
        self.splitter.setStretchFactor(0,0); self.splitter.setStretchFactor(1,1)
        layout.addWidget(self.splitter)
        self._restore_splitter()
        self.splitter.splitterMoved.connect(lambda *args: self._save_splitter())
        self.map.clicked.connect(self.map_click); self.map.moved.connect(self.move_point); self.map.deleted.connect(self.delete_point); self.map.deleted_many.connect(self.delete_selected_points); self.map.selection_changed.connect(lambda points:self.delete_selected.setEnabled(bool(points) and not self.busy)); self.map.inserted.connect(self.insert_point)
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.name.editingFinished.connect(self.rename)
        menu = self.menuBar().addMenu('File')
        for title, shortcut, fn in [('New route',QKeySequence.StandardKey.New,self.new_route),('Open GPX…',QKeySequence.StandardKey.Open,self.open_gpx),('Open reference GPX…',None,self.open_reference),('Export GPX',QKeySequence.StandardKey.Save,self.save_gpx)]:
            action = QAction(title,self)
            if shortcut: action.setShortcut(shortcut)
            action.triggered.connect(fn); menu.addAction(action)
        edit = self.menuBar().addMenu('Edit')
        for title,shortcut,fn in [('Undo',QKeySequence.StandardKey.Undo,self.undo_route),('Redo',QKeySequence.StandardKey.Redo,self.redo_route)]:
            action=QAction(title,self); action.setShortcut(shortcut); action.triggered.connect(fn); edit.addAction(action)
        self._delete_action=QAction('Delete selected',self); self._delete_action.setShortcuts([QKeySequence.StandardKey.Delete, QKeySequence(Qt.Key.Key_Backspace)])
        self._delete_action.triggered.connect(lambda _checked=False: self.delete_selected_points()); edit.addAction(self._delete_action)
        # Window-level shortcuts so Delete/Backspace/Escape work even when the
        # map widget itself does not have keyboard focus. These MUST be kept
        # referenced (self._delete_shortcuts) — orphaned QShortcuts are garbage
        # collected and silently stop firing.
        self._delete_shortcuts=[]
        for sequence in (QKeySequence.StandardKey.Delete, QKeySequence(Qt.Key.Key_Backspace)):
            shortcut=QShortcut(sequence,self); shortcut.setContext(Qt.ShortcutContext.WindowShortcut); shortcut.activated.connect(lambda: self.delete_selected_points())
            self._delete_shortcuts.append(shortcut)
        self._clear_selection_shortcut=QShortcut(QKeySequence(Qt.Key.Key_Escape),self); self._clear_selection_shortcut.setContext(Qt.ShortcutContext.WindowShortcut); self._clear_selection_shortcut.activated.connect(self.clear_point_selection)
        view = self.menuBar().addMenu('View')
        view.addAction('Fit route',lambda:self.map.fit(self.route.geometry))
        view.addAction('Hide reference',self.hide_reference)
        view.addAction('Reset panel width',self._reset_splitter)
        maps=self.menuBar().addMenu('Map data')
        maps.addAction('Use offline map…',self.choose_offline_map)
        maps.addAction('Worldwide maps (automatic downloads)',self.use_downloaded_maps)
        settings_menu=self.menuBar().addMenu('Settings')
        settings_menu.addAction('GPX export folder…',self.choose_export_folder)
        self.map_source=QLabel(); self.map_source.setObjectName('muted'); self.map_source.setWordWrap(True)
        footer_box.insertWidget(1,self.map_source)
        self.update_map_source()
        self.store = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))/'draft.json'
        self.restore()
        self.change_mode(); self.refresh()

    @staticmethod
    def disclosure(layout,title):
        toggle=QPushButton('▸ '+title); toggle.setObjectName('disclosure'); toggle.setCheckable(True)
        content=QWidget(); inner=QVBoxLayout(content); inner.setContentsMargins(0,4,0,6); inner.setSpacing(8)
        content.hide(); layout.addWidget(toggle); layout.addWidget(content)
        def changed(opened):
            content.setVisible(opened); toggle.setText(('▾ ' if opened else '▸ ')+title)
        toggle.toggled.connect(changed)
        return toggle,inner

    def update_map_source(self):
        self.map_source.setText(f'Offline routing · {Path(mapdata.offline_path).name}' if mapdata.offline_path
                               else 'Worldwide routing · maps cached locally')

    def _restore_splitter(self):
        try:
            state = QSettings('ParkLoop','ParkLoop').value('ui/splitter')
            if state and self.splitter.restoreState(state):
                return
        except (RuntimeError, TypeError, AttributeError):
            pass
        self.splitter.setSizes([350, max(500, self.width() - 350)])

    def _save_splitter(self):
        try:
            QSettings('ParkLoop','ParkLoop').setValue('ui/splitter', self.splitter.saveState())
        except (OSError, RuntimeError):
            pass

    def _reset_splitter(self):
        try:
            QSettings('ParkLoop','ParkLoop').remove('ui/splitter')
        except (OSError, RuntimeError):
            pass
        self.splitter.setSizes([350, max(500, self.width() - 350)])
        self.status.setText('Panel width reset. Drag the divider to resize it.')

    def choose_offline_map(self):
        if self.busy:return
        path,_=QFileDialog.getOpenFileName(self,'Use offline routing map','','Overpass map data (*.json)')
        if not path:return
        previous=mapdata.offline_path
        mapdata.offline_path=path
        try:mapdata.offline_elements()
        except ValueError as exc:
            mapdata.offline_path=previous; self.failed(str(exc)); return
        QSettings('ParkLoop','ParkLoop').setValue('maps/offline_path',path)
        self.router._cached=None; self.update_map_source()
        self.status.setText('Offline routing enabled. Routes must stay within the saved map area.')

    def use_downloaded_maps(self):
        if self.busy:return
        mapdata.offline_path=None
        QSettings('ParkLoop','ParkLoop').remove('maps/offline_path')
        self.router._cached=None; self.update_map_source()
        self.status.setText('Worldwide routing enabled. Maps download for your chosen area and are saved locally for reuse.')

    def update_routing_note(self):
        self.routing_note.setText([
            'Shortest allowed route, including roads with missing sidewalk information.',
            'Connects points directly, without following paths.',
            'Only mapped pedestrian paths or confirmed sidewalks. May be longer or unavailable.'
        ][self.snap.currentIndex()])

    def editable_copy(self):
        route=copy.deepcopy(self.route)
        route.elevations=[list(route.elevations[i]) if i<len(route.elevations) and len(route.elevations[i])==len(seg) else [None]*len(seg) for i,seg in enumerate(route.segments)]
        return route

    def rename(self):
        if self.busy: return
        candidate=self.editable_copy(); candidate.name=self.name.text().strip() or 'Untitled run'; self.commit(candidate)

    def restore(self):
        try:
            data=json.loads(self.store.read_text()); self.route=Route(**data)
            for p in self.route.geometry: validate_coords(*p)
            self.name.setText(self.route.name); self.details.setText(self.route.description)
            if self.route.geometry: self.start=self.route.geometry[0]; self.map.fit(self.route.geometry)
        except (OSError,ValueError,TypeError,KeyError): self.route=Route()
        self.remember_start(self.start)
        self.location.setText(self.format_start(self.start))

    def persist(self):
        try:
            self.store.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.store.with_suffix('.tmp'); tmp.write_text(json.dumps(asdict(self.route)),encoding='utf-8'); tmp.replace(self.store)
        except OSError: self.status.setText('Draft could not be saved. Export GPX to keep this route.')

    def refresh(self):
        self.map.route=self.route; self.map.start=self.start; self.map.update()
        self.stats.setText(f'{self.route.distance_m/1000:.2f} km')
        self.undo.setEnabled(bool(self.history) and not self.busy); self.redo.setEnabled(bool(self.future) and not self.busy)
        self.export.setEnabled(self.route.distance_m>0 and not self.busy)
        self.check_button.setEnabled(self.route.distance_m>0 and not self.busy)
        self.close_loop.setEnabled(not self.busy and len(self.route.segments)==1 and len(self.route.geometry)>1)
        self.delete_selected.setEnabled(bool(self.map.selected) and not self.busy)
        has_alts = bool(self.alternatives)
        self.use_alt.setEnabled(has_alts and not self.busy and self.alt_list.currentRow() >= 0)
        self.dismiss_alt.setEnabled(has_alts and not self.busy)
        self._select_recent_match()

    def change_mode(self):
        self.pick_start.setChecked(False)
        auto=self.mode.currentIndex()==1; self.map.edit=not auto
        self.generate.setVisible(auto); self.distance.setEnabled(auto); self.preference.setEnabled(auto)
        self.segment_label.setVisible(not auto); self.target_label.setVisible(auto); self.distance.setVisible(auto); self.preference_label.setVisible(auto); self.preference.setVisible(auto)
        self.snap.setVisible(not auto); self.close_loop.setVisible(not auto)
        self.routing_note.setVisible(not auto)
        self.hint.setObjectName('muted')
        self.hint.setText('Choose a start on the map or use coordinates, set the distance and preference, then find route options.' if auto else 'Set a start first. Click to extend, drag points to adjust.\nCtrl/Cmd-drag selects a group · Delete removes it and rejoins the gap via the shortest path.')
        self.map.update()

    def toggle_start_picker(self,enabled):
        self.map.pick_start=enabled
        self.pick_start.setText('Cancel choosing start' if enabled else 'Choose start on map')
        self.map.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.status.setText('Click the map once to set the starting point.' if enabled else 'Start selection closed.')

    def toggle_point_selection(self,enabled):
        self.map.select_mode=enabled
        self.map.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        if not enabled:
            self.map.selected.clear(); self.map.selection_changed.emit(set()); self.map.update()
        self.select_points.setText('Done selecting' if enabled else 'Select points')
        self.status.setText('Click points or drag a rectangle to select them, then Delete (Esc clears).' if enabled else 'Point selection closed.')

    def set_location(self):
        if self.busy:return
        self.pick_start.setChecked(False)
        self.select_points.setChecked(False)
        try:
            point=tuple(map(float,self.location.text().split(',')))
            if len(point)!=2: raise ValueError('Enter latitude, longitude.')
            validate_coords(*point)
            if self.mode.currentIndex()==0 and self.route.geometry:
                self.move_point(0,0,*point); return
            self.start=point; self.map.center=point
            self.remember_start(point)
            if self.mode.currentIndex()==0:
                candidate=self.editable_copy(); candidate.segments=[[point]]; self.commit(candidate); return
            if self.alternatives:
                self.clear_alternatives_panel()
            self.refresh()
        except ValueError as exc:self.failed(str(exc))

    @staticmethod
    def format_start(point):
        return f'{point[0]:.6f}, {point[1]:.6f}'

    def _load_recent_starts(self):
        try:
            raw = QSettings('ParkLoop','ParkLoop').value('start/history', '')
        except (OSError, RuntimeError):
            raw = ''
        points = []
        if raw:
            try:
                for item in json.loads(raw):
                    lat, lon = float(item[0]), float(item[1])
                    validate_coords(lat, lon)
                    key = (round(lat, 6), round(lon, 6))
                    if key not in points:
                        points.append(key)
            except (ValueError, TypeError, IndexError, KeyError):
                pass
        return points[:self.MAX_RECENT_STARTS]

    def _save_recent_starts(self):
        try:
            QSettings('ParkLoop','ParkLoop').setValue('start/history', json.dumps(self.recent_starts))
        except (OSError, RuntimeError):
            pass

    def _load_place_labels(self):
        try:
            labels=json.loads(QSettings('ParkLoop','ParkLoop').value('start/labels','{}'))
            return {key:value for key,value in labels.items() if isinstance(value,str)}
        except (ValueError,TypeError,AttributeError,RuntimeError,OSError):
            return {}

    def _pin_labeled_starts(self):
        """Keep saved places above ordinary history without changing group order."""
        labeled=[]; recent=[]
        for point in self.recent_starts:
            (labeled if self.format_start(point) in self.place_labels else recent).append(point)
        self.recent_starts=labeled+recent

    def _sync_place_label(self):
        if not hasattr(self,'place_label'):return
        point=self.recent_combo.currentData()
        key=self.format_start(point) if point is not None else None
        if key!=self._label_point:
            self.place_label.setText(self.place_labels.get(key,''))
            self._label_point=key
        self.place_label.setEnabled(key is not None and not self.busy)
        self.save_place_label.setEnabled(key is not None and not self.busy)

    def save_recent_label(self):
        if self.busy:return
        self.select_points.setChecked(False)
        point=self.recent_combo.currentData()
        if point is None:return
        key=self.format_start(point); label=self.place_label.text().strip()
        was_labeled=key in self.place_labels
        if label:self.place_labels[key]=label
        else:self.place_labels.pop(key,None)
        QSettings('ParkLoop','ParkLoop').setValue('start/labels',json.dumps(self.place_labels))
        self.place_label.setText(label)
        if label and not was_labeled:
            self.recent_starts=[p for p in self.recent_starts if p!=point]
            self.recent_starts.insert(0,point)
        self._pin_labeled_starts()
        self._save_recent_starts()
        self._refresh_recent_combo()
        self.status.setText('Place label saved.' if label else 'Place label removed. Coordinates will be shown.')

    def remember_start(self, point):
        try:
            validate_coords(*point)
        except ValueError:
            return
        key = (round(point[0], 6), round(point[1], 6))
        # Labeled places are pinned, so using one again must not add or move it
        # in the ordinary recency history.
        if key in self.recent_starts and self.format_start(key) in self.place_labels:
            self._select_recent_match()
            return
        self.recent_starts = [p for p in self.recent_starts if p != key]
        self.recent_starts.insert(0, key)
        self._pin_labeled_starts()
        self.recent_starts = self.recent_starts[:self.MAX_RECENT_STARTS]
        self._save_recent_starts()
        self._refresh_recent_combo()

    def _refresh_recent_combo(self):
        if not hasattr(self, 'recent_combo'):
            return
        self._syncing_recent = True
        try:
            self.recent_combo.blockSignals(True)
            self.recent_combo.clear()
            for point in self.recent_starts:
                coordinates=self.format_start(point)
                self.recent_combo.addItem(self.place_labels.get(coordinates) or coordinates, point)
                self.recent_combo.setItemData(self.recent_combo.count()-1,coordinates,Qt.ItemDataRole.ToolTipRole)
            self.recent_combo.setEnabled(bool(self.recent_starts) and not self.busy)
            self._select_recent_match()
        finally:
            self.recent_combo.blockSignals(False)
            self._syncing_recent = False

    def _select_recent_match(self):
        if not hasattr(self, 'recent_combo'):
            return
        try:
            current = (round(self.start[0], 6), round(self.start[1], 6))
        except (TypeError, IndexError):
            return
        match = next((i for i, p in enumerate(self.recent_starts) if p == current), -1)
        self.recent_combo.blockSignals(True)
        try:
            self.recent_combo.setCurrentIndex(match)
        finally:
            self.recent_combo.blockSignals(False)
        self._sync_place_label()

    def _recent_start_chosen(self, index):
        if self._syncing_recent or self.busy:
            return
        if index < 0 or index >= len(self.recent_starts):
            return
        self.pick_start.setChecked(False)
        point = self.recent_starts[index]
        if self.mode.currentIndex()==0 and self.route.geometry:
            self.move_point(0,0,*point); return
        self.start = point
        self.location.setText(self.format_start(point))
        self.map.center = point
        # Ordinary coordinates move up by recency; labeled places stay pinned.
        self.remember_start(point)
        if self.mode.currentIndex()==0 and not self.route.geometry:
            candidate=self.editable_copy(); candidate.segments=[[point]]; self.commit(candidate); return
        if self.alternatives:
            self.clear_alternatives_panel()
        self.status.setText('Starting point set from recent list.')
        self.refresh()

    def set_busy(self,value):
        self.busy=value; self.map.busy=value
        self.pick_start.setEnabled(not value)
        if value:self.pick_start.setChecked(False)
        for widget in (self.mode,self.name,self.location,self.locate,self.recent_combo,self.generate,self.close_loop,self.snap,self.distance,self.preference):widget.setEnabled(not value)
        self.alt_list.setEnabled(not value); self.use_alt.setEnabled(not value and bool(self.alternatives)); self.dismiss_alt.setEnabled(not value and bool(self.alternatives))
        self.progress.setVisible(value); self.cancel_button.setVisible(value); self.refresh()
        if not value:self.change_mode()

    def job(self,fn,callback):
        if self.busy:return
        self.cancel=threading.Event(); self.callback=callback; self.set_busy(True)
        self.status.setText('Working…')
        def run():
            try:
                result=fn()
                if self.cancel.is_set():self.events.failed.emit('Cancelled. Your route was kept unchanged.')
                else:self.events.complete.emit(result)
            except Exception as exc:self.events.failed.emit(str(exc))
        threading.Thread(target=run,daemon=True).start()

    def completed(self,result):
        self.set_busy(False); self.callback(result)

    def failed(self,message):
        self.sync_route_start(); self.set_busy(False); self.status.setText(message)

    def cancel_job(self):
        self.cancel.set(); self.status.setText('Cancelling after the current map request…')

    def commit(self,route):
        if route.geometry and self.mode.currentIndex()==0:
            self.start=route.geometry[0]; self.location.setText(f'{self.start[0]:.6f}, {self.start[1]:.6f}'); self.remember_start(self.start)
        self.safety_note.setText('Road safety: not checked for this version.')
        self.history.append(copy.deepcopy(self.route)); self.history=self.history[-40:]; self.future=[]
        self.route=route; self.name.setText(route.name); self.details.setText(route.description or 'Manual route · GPX track geometry')
        self.status.setText('Route updated. Draft saved locally.'); self.persist(); self.refresh()
        if not self._applying_alt:
            self.clear_alternatives_panel()

    def undo_route(self):
        if self.busy or not self.history:return
        self.safety_note.setText('Road safety: not checked for this version.')
        self.future.append(self.route); self.route=self.history.pop(); self.sync_route_start(); self.name.setText(self.route.name); self.details.setText(self.route.description); self.persist(); self.refresh()

    def redo_route(self):
        if self.busy or not self.future:return
        self.safety_note.setText('Road safety: not checked for this version.')
        self.history.append(self.route); self.route=self.future.pop(); self.sync_route_start(); self.name.setText(self.route.name); self.details.setText(self.route.description); self.persist(); self.refresh()

    def sync_route_start(self):
        if self.mode.currentIndex()==0 and self.route.geometry:
            self.start=self.route.geometry[0]
            self.location.setText(self.format_start(self.start))

    def map_click(self,lat,lon):
        if self.busy:return
        if self.pick_start.isChecked():
            self.pick_start.setChecked(False)
            if self.mode.currentIndex()==0 and self.route.geometry:
                self.move_point(0,0,lat,lon); return
            self.start=(lat,lon); self.location.setText(f'{lat:.6f}, {lon:.6f}'); self.remember_start(self.start)
            if self.mode.currentIndex()==0:
                candidate=self.editable_copy(); candidate.segments=[[(lat,lon)]]; self.commit(candidate); return
            if self.alternatives:
                self.clear_alternatives_panel()
            self.refresh(); return
        if self.mode.currentIndex()==1:return
        candidate=self.editable_copy()
        if not candidate.segments:
            self.status.setText('Choose Set start on map first, or use the coordinates button.'); return
        last=candidate.segments[-1][-1]
        if self.snap.currentIndex()==1:
            candidate.segments[-1].append((lat,lon)); candidate.elevations[-1].append(None); candidate.description='Manual GPX edit; road and no-repeat checks need repeating.'; self.commit(candidate)
        else:
            def work():
                routed=self.router.calculate_segment(last,(lat,lon))
                part=routed.geometry
                if not part:raise ValueError('No walking path found.')
                from .core import haversine_m
                if len(candidate.segments[-1])==1:
                    candidate.segments[-1][0]=part[0]; candidate.elevations[-1][0]=None
                elif haversine_m(*last, *part[0]) > 0.2:
                    raise ValueError('The existing endpoint is not on the checked path. Edit the endpoint first; no straight connector was added.')
                candidate.segments[-1].extend(part[1:]); candidate.elevations[-1].extend([None]*(len(part)-1))
                candidate.description=f'Latest leg: {routed.description} Check road safety for the complete edited track.'
                return candidate
            self.job(work,self.commit)

    def move_point(self,i,j,lat,lon):
        if self.busy:return
        if self.snap.currentIndex()!=1 and len(self.route.segments[i])>1:
            self.reroute_edit(i,j,(lat,lon)); return
        candidate=self.editable_copy(); candidate.segments[i][j]=(lat,lon); candidate.elevations[i][j]=None; candidate.description='Edited GPX geometry; moved points are not re-snapped.'; self.commit(candidate)

    # A loop counts as closed when its ends meet within this tolerance, so a
    # routed closure (whose snapped endpoint may drift by metres) is recognised.
    CLOSED_LOOP_TOL_M = 10.0

    @staticmethod
    def _loop_closed(points):
        from .core import haversine_m
        return len(points) >= 2 and haversine_m(*points[0], *points[-1]) <= Window.CLOSED_LOOP_TOL_M

    @staticmethod
    def _contiguous_blocks(indices):
        blocks = []
        for idx in indices:
            if blocks and idx == blocks[-1][1] + 1:
                blocks[-1][1] = idx
            else:
                blocks.append([idx, idx])
        return [(a, b) for a, b in blocks]

    @staticmethod
    def _splice_deletion(candidate, by_seg, routes):
        """Remove deleted indices, splicing routed bridges over interior gaps.

        ``routes`` maps ``(segment, first_deleted, last_deleted)`` to the
        replacement geometry covering the gap (anchors included).
        """
        new_segments, new_elevations = [], []
        for i, seg in enumerate(candidate.segments):
            elev = candidate.elevations[i]
            delset = by_seg.get(i)
            if not delset:
                new_segments.append(seg); new_elevations.append(elev); continue
            bridged = sorted((a, b) for (ii, a, b) in routes if ii == i)
            out_p, out_e = [], []
            prev = 0
            for a, b in bridged:
                for idx in range(prev, a - 1):
                    if idx not in delset:
                        out_p.append(seg[idx]); out_e.append(elev[idx])
                part = routes[(i, a, b)]
                out_p.extend(part); out_e.extend([None] * len(part))
                prev = b + 2
            for idx in range(prev, len(seg)):
                if idx not in delset:
                    out_p.append(seg[idx]); out_e.append(elev[idx])
            if out_p:
                new_segments.append(out_p); new_elevations.append(out_e)
        candidate.segments = new_segments
        candidate.elevations = new_elevations

    def _reclose_loop_straight(self, candidate):
        """Re-append the start point for straight-line mode. Returns True if closed."""
        if len(candidate.segments) == 1 and len(candidate.segments[0]) >= 2 \
                and not self._loop_closed(candidate.segments[0]):
            candidate.segments[0].append(candidate.segments[0][0])
            candidate.elevations[0].append(None)
            return True
        return False

    def delete_point(self,i,j):
        if self.busy:return
        # All deletions share the bulk path: the points are always removed
        # locally, while gap bridging / loop re-closing is best-effort routing.
        self.delete_selected_points({(i, j)})

    def delete_selected_points(self, points=None):
        if self.busy:return
        # Guard against Qt passing clicked(bool) when wired to a button.
        if isinstance(points, bool):
            points = None
        points=set(self.map.selected if points is None else points)
        if not points:return
        candidate=self.editable_copy()
        by_seg = {}
        for item in points:
            try:
                i, j = item
            except (TypeError, ValueError):
                continue
            if isinstance(i, bool) or isinstance(j, bool) or not isinstance(i, int) or not isinstance(j, int):
                continue
            if 0 <= i < len(candidate.segments) and 0 <= j < len(candidate.segments[i]):
                by_seg.setdefault(i, set()).add(j)
        if not by_seg:
            self.status.setText('Selected points are no longer on the route.')
            self.map.selected.clear(); self.map.selection_changed.emit(set()); self.refresh()
            return
        straight = self.snap.currentIndex() == 1
        was_loop = (len(candidate.segments) == 1 and len(candidate.segments[0]) >= 2
                    and self._loop_closed(candidate.segments[0]))
        touched_end = was_loop and bool(by_seg.get(0, set()) & {0, len(candidate.segments[0]) - 1})
        # Interior blocks leave a gap whose surviving neighbours are rejoined
        # with the shortest walking path (routing modes only).
        gap_plan = {}
        if not straight:
            for i, delset in by_seg.items():
                n = len(candidate.segments[i])
                for a, b in self._contiguous_blocks(sorted(delset)):
                    if a > 0 and b < n - 1:
                        gap_plan.setdefault(i, []).append(
                            (a, b, candidate.segments[i][a - 1], candidate.segments[i][b + 1]))
        if not gap_plan and not (touched_end and not straight):
            self._splice_deletion(candidate, by_seg, {})
            reclosed = touched_end and straight and self._reclose_loop_straight(candidate)
            candidate.description = ('Selected route points deleted. Loop re-closed.'
                                     if reclosed else 'Selected route points deleted. Check road safety for the updated track.')
            self.map.selected.clear(); self.map.selection_changed.emit(set()); self.commit(candidate)
            return
        def work():
            # Best effort: the deletion always succeeds locally. Each gap is
            # bridged with the shortest walking path when available, otherwise
            # its neighbours are joined directly so Del never no-ops offline.
            from .core import haversine_m
            routes = {}
            bridged, direct = 0, 0
            for i, blocks in gap_plan.items():
                for a, b, left, right in blocks:
                    if haversine_m(*left, *right) < 0.2:
                        routes[(i, a, b)] = [left]; bridged += 1; continue
                    try:
                        routed = self.router.calculate_route([left, right])
                        if not routed.geometry:
                            raise ValueError('No walking path found.')
                        routes[(i, a, b)] = list(routed.geometry); bridged += 1
                    except Exception:
                        direct += 1
            self._splice_deletion(candidate, by_seg, routes)
            reclosed, left_open = False, False
            if touched_end and len(candidate.segments) == 1 and len(candidate.segments[0]) >= 2 \
                    and not self._loop_closed(candidate.segments[0]):
                end, start = candidate.segments[0][-1], candidate.segments[0][0]
                if haversine_m(*end, *start) < 0.2:
                    candidate.segments[0].append(start); candidate.elevations[0].append(None); reclosed = True
                else:
                    try:
                        leg = self.router.calculate_route([end, start]).geometry
                        if not leg:
                            raise ValueError('No walking path found.')
                        candidate.segments[0].extend(leg[1:]); candidate.elevations[0].extend([None] * (len(leg) - 1)); reclosed = True
                    except Exception:
                        left_open = True
            if reclosed:
                candidate.description = 'Selected route points deleted. Loop re-closed via the shortest walking path.'
            elif left_open:
                candidate.description = ('Selected route points deleted. Loop left open: no walking path found to re-close it. '
                                         'Use Close loop when map data is available.')
            elif bridged and direct:
                candidate.description = ('Selected route points deleted. Some gaps re-joined via the shortest walking path; '
                                         'the rest were joined directly (no path found). Check road safety for the updated track.')
            elif bridged:
                candidate.description = 'Selected route points deleted. Gaps re-joined via the shortest walking path. Check road safety for the updated track.'
            else:
                candidate.description = ('Selected route points deleted. No walking path available; points joined directly. '
                                         'Check road safety for the updated track.')
            return candidate
        self.map.selected.clear(); self.map.selection_changed.emit(set())
        self.job(work, self.commit)

    def clear_point_selection(self):
        if self.busy:return
        if self.map.selected:
            self.map.selected.clear(); self.map.selection_changed.emit(set()); self.map.update(); self.refresh()
            self.status.setText('Selection cleared.')

    def insert_point(self,i,j,lat,lon):
        if self.busy:return
        if self.snap.currentIndex()!=1:
            self.reroute_edit(i,j,(lat,lon),insert=True); return
        candidate=self.editable_copy(); candidate.segments[i].insert(j,(lat,lon)); candidate.elevations[i].insert(j,None); candidate.description='Edited GPX geometry'; self.commit(candidate)

    def reroute_edit(self,i,j,point,*,insert=False):
        candidate=self.editable_copy()
        segment=candidate.segments[i]
        left=max(0,j-1); right=min(len(segment)-1,j if insert else j+1)
        anchors=([segment[left]] if j>0 else [])
        if point is not None: anchors.append(point)
        if insert or j<len(segment)-1: anchors.append(segment[right])
        def work():
            from .core import haversine_m
            routed=self.router.calculate_route(anchors)
            part=routed.geometry
            if not part: raise ValueError('No walking path found. Your route was kept unchanged.')
            if left>0 and haversine_m(*segment[left],*part[0])>0.2:
                raise ValueError('The preceding point is not on a verified path.')
            if right<len(segment)-1 and haversine_m(*segment[right],*part[-1])>0.2:
                raise ValueError('The following point is not on a verified path.')
            candidate.segments[i]=segment[:left]+part+segment[right+1:]
            candidate.elevations[i]=candidate.elevations[i][:left]+[None]*len(part)+candidate.elevations[i][right+1:]
            candidate.description=f'Recalculated section: {routed.description} Check road safety for the complete track.'
            return candidate
        self.job(work,self.commit)

    def close_route(self):
        if self.busy or len(self.route.segments)!=1 or len(self.route.geometry)<2:return
        self.map_click(*self.route.segments[0][0])

    def auto(self):
        if self.busy:return
        target=self.distance.value()*1000; preference=self.preference.currentIndex(); start=self.start
        preference_name=self.preference.currentText()
        started=datetime.now(timezone.utc).isoformat()
        log_path=write_auto_route_log(
            f'ParkLoop Auto route diagnostic\nStarted: {started}\n'
            f'Python: {sys.version.split()[0]}\nPlatform: {platform.platform()}\n'
            f'Start: {start[0]:.7f}, {start[1]:.7f}\nTarget: {target:.0f} m\n'
            f'Preference: {preference_name}\n'
            f'Offline map: {os.environ.get("PARKLOOP_OFFLINE_MAP") or mapdata.offline_path or "none"}',
            reset=True)
        def diagnostic(message):
            timestamp=datetime.now(timezone.utc).isoformat()
            write_auto_route_log(f'[{timestamp}] {message}')
        def report_progress(message):
            diagnostic(f'Progress: {message}')
            self.events.progress.emit(message)
        def work():
            try:
                result=generate_alternatives(start,target,cancel=self.cancel,
                    progress=report_progress,strict=preference==1,prefer_parks=preference!=2,
                    diagnostic=diagnostic)
                diagnostic(f'SUCCESS: {len(result)} alternative(s) generated')
                return result
            except Exception as exc:
                diagnostic(f'FAILED: {type(exc).__name__}: {exc}\n{traceback.format_exc()}')
                if log_path:
                    raise RoutingError(f'{exc}\nDebug log: {log_path}') from exc
                raise
        self.job(work,self.show_alternatives)

    @staticmethod
    def _alt_label(alternative, index):
        r, a = alternative.result, alternative.audit
        distance = r.route.distance_m
        dev = distance / alternative.target_m - 1
        flag = '! unverified' if a.unknown_m > 0 else 'checked'
        return (f'Alt {index+1} · {distance/1000:.2f} km ({dev:+.1%}) · '
                f'{r.park_fraction:.0%} green · {r.turn_count} turns · {flag}')

    def show_alternatives(self, alternatives):
        if not alternatives:
            self.failed('No alternatives found.'); return
        if self.pre_alt_route is None:
            self.pre_alt_route = copy.deepcopy(self.route)
        self.alternatives = list(alternatives)
        self.alt_list.blockSignals(True)
        self.alt_list.clear()
        for i, alt in enumerate(self.alternatives):
            self.alt_list.addItem(QListWidgetItem(self._alt_label(alt, i)))
        self.alt_list.blockSignals(False)
        for widget in (self.alt_title, self.alt_list, self.alt_details, self.use_alt, self.dismiss_alt):
            widget.show()
        self.alt_title.setText(f'{len(self.alternatives)} alternative(s) · select to preview on the map')
        self.alt_list.setCurrentRow(0)
        # currentRowChanged fires preview; ensure first preview even if signal missed.
        self.preview_alternative(0)
        self.refresh()

    def preview_alternative(self, row):
        if self._previewing or not self.alternatives:
            return
        if row < 0 or row >= len(self.alternatives):
            return
        if self.busy:
            return
        self._previewing = True
        try:
            alternative = self.alternatives[row]
            route = copy.deepcopy(alternative.result.route)
            self.route = route
            if route.geometry:
                self.start = route.geometry[0]
                self.location.setText(f'{self.start[0]:.6f}, {self.start[1]:.6f}')
            self.name.setText(route.name)
            self.details.setText(alternative.summary)
            audit = alternative.audit
            reasons = list(dict.fromkeys(i['reason'] for i in audit.issues))
            if reasons:
                self.alt_details.setText(' · '.join(reasons))
            else:
                self.alt_details.setText('All sections have map evidence for pedestrian use.')
            self.safety_note.setText('Contains sections without map verification.' if audit.unknown_m else 'Map check passed: paths / tagged sidewalks.')
            self.map.fit(route.geometry)
            self.refresh()
            self.status.setText(f'Previewing alternative {row+1} of {len(self.alternatives)}. Switch in the list, then Use this route.')
        finally:
            self._previewing = False

    def use_selected_alternative(self):
        row = self.alt_list.currentRow()
        if self.busy or not self.alternatives or row < 0 or row >= len(self.alternatives):
            return
        alternative = self.alternatives[row]
        route = copy.deepcopy(alternative.result.route)
        self._applying_alt = True
        try:
            self.commit(route)
        finally:
            self._applying_alt = False
        self.pre_alt_route = None
        if route.geometry:
            self.start = route.geometry[0]
            self.location.setText(f'{self.start[0]:.6f}, {self.start[1]:.6f}')
            self.remember_start(self.start)
        self.map.fit(route.geometry)
        self.details.setText(alternative.summary)
        self.alt_details.setText(self.details.text())
        self.safety_note.setText('Contains sections without map verification.' if alternative.audit.unknown_m else 'Map check passed: paths / tagged sidewalks.')
        self.status.setText('Alternative saved. Switch to Manual editor to adjust it, or preview another alternative.')
        self.refresh()

    def dismiss_alternatives(self):
        if self.busy:
            return
        if self.pre_alt_route is not None:
            self.route = self.pre_alt_route
            self.pre_alt_route = None
            self.name.setText(self.route.name)
            self.details.setText(self.route.description or 'Manual route · GPX track geometry')
            self.safety_note.setText('Road safety: not checked for this version.')
            if self.route.geometry:
                self.start = self.route.geometry[0]
                self.location.setText(f'{self.start[0]:.6f}, {self.start[1]:.6f}')
            self.map.fit(self.route.geometry)
            self.status.setText('Alternatives dismissed. Previous route restored.')
        else:
            self.status.setText('Alternatives panel closed. Current route kept.')
        self.clear_alternatives_panel()
        self.refresh()

    def clear_alternatives_panel(self):
        self.alternatives = []
        self.pre_alt_route = None
        if hasattr(self, 'alt_list'):
            self.alt_list.blockSignals(True)
            self.alt_list.clear()
            self.alt_list.blockSignals(False)
            for widget in (self.alt_title, self.alt_list, self.alt_details, self.use_alt, self.dismiss_alt):
                widget.hide()
        if hasattr(self, 'map') and hasattr(self, 'undo'):
            self.refresh()

    # Kept for backward compatibility (tests / external callers).
    def choose_alternative(self, alternatives):
        self.show_alternatives(alternatives)

    def check_road_safety(self):
        if self.busy or self.route.distance_m<=0:return
        route=copy.deepcopy(self.route)
        def work():
            from .core import haversine_m
            points=route.geometry
            center=((min(p[0] for p in points)+max(p[0] for p in points))/2,
                    (min(p[1] for p in points)+max(p[1] for p in points))/2)
            radius=max(haversine_m(*center,*p) for p in points)
            if radius>15000:raise ValueError('Road checking currently covers routes within a 30 km-wide area.')
            self.events.progress.emit('Checking current road, sidewalk and pedestrian-access tags…')
            return audit_route(route,fetch_elements(center,max(1000,2*radius)),include_unverified=True,include_major=True)
        def done(report):
            report_path=self.store.parent/'last-road-safety-check.json'
            try:
                report_path.parent.mkdir(parents=True,exist_ok=True)
                report_path.write_text(json.dumps(asdict(report),indent=2),encoding='utf-8')
            except OSError:pass
            if report.passed:
                self.safety_note.setText('Map check passed: pedestrian paths / tagged sidewalks. Check current conditions.')
                self.status.setText(f'All {report.checked_m/1000:.2f} km matched eligible mapped paths or sidewalks.')
            else:
                self.safety_note.setText(f'Not verified: {report.rejected_m:.0f} m excluded; {report.unknown_m:.0f} m without map verification.')
                reasons=list(dict.fromkeys(issue['reason'] for issue in report.issues))
                self.status.setText(' · '.join(reasons[:3]))
        self.job(work,done)

    def new_route(self):
        if not self.busy:self.commit(Route())

    def open_gpx(self):
        if self.busy:return
        path,_=QFileDialog.getOpenFileName(self,'Open GPX','','GPX files (*.gpx)')
        if path:
            try:
                route=read_gpx(path); self.commit(route); self.start=route.geometry[0]; self.location.setText(f'{self.start[0]:.6f}, {self.start[1]:.6f}'); self.remember_start(self.start); self.map.fit(route.geometry); self.refresh()
            except Exception as exc:self.failed(str(exc))

    def open_reference(self):
        if self.busy:return
        path,_=QFileDialog.getOpenFileName(self,'Open reference route','','GPX files (*.gpx)')
        if path:
            try:self.map.reference=read_gpx(path); self.map.fit(self.map.reference.geometry); self.status.setText('Reference overlay shown. Your editable route uses the theme accent color.')
            except Exception as exc:self.failed(str(exc))

    def hide_reference(self):
        self.map.reference=None; self.map.update()

    def export_folder(self):
        saved=QSettings('ParkLoop','ParkLoop').value('export/folder','')
        return Path(saved).expanduser() if saved else Path.home()/'gpx'

    def saved_exports(self):
        try:
            data=json.loads(QSettings('ParkLoop','ParkLoop').value('export/history','[]'))
            return list(dict.fromkeys(p for p in data if isinstance(p,str)))[:5] if isinstance(data,list) else []
        except (ValueError,TypeError):return []

    def refresh_recent_exports(self):
        self.recent_exports.clear()
        paths=self.saved_exports()
        for path in paths:
            item=QListWidgetItem(Path(path).name)
            item.setToolTip(path); item.setData(Qt.ItemDataRole.UserRole,path)
            self.recent_exports.addItem(item)
        if not paths:
            item=QListWidgetItem('No exports yet'); item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_exports.addItem(item)

    def remember_export(self,path):
        path=str(path.resolve())
        history=[path]+[p for p in self.saved_exports() if p!=path]
        QSettings('ParkLoop','ParkLoop').setValue('export/history',json.dumps(history[:5]))
        self.refresh_recent_exports()

    def choose_export_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'Choose GPX export folder',str(self.export_folder()))
        if folder:
            QSettings('ParkLoop','ParkLoop').setValue('export/folder',folder)
            self.status.setText(f'GPX exports will be saved in {folder}')

    @staticmethod
    def gpx_filename(name):
        name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name.strip()).strip(' .') or 'Untitled run'
        if name.lower().endswith('.gpx'):name=name[:-4].rstrip(' .') or 'Untitled run'
        if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
            name='_'+name
        return name[:100]+'.gpx'

    def save_gpx(self):
        if self.busy:return
        try:
            name=self.name.text().strip() or 'Untitled run'
            folder=self.export_folder(); folder.mkdir(parents=True,exist_ok=True)
            path=folder/self.gpx_filename(name)
            if path.exists() and QMessageBox.question(self,'Replace GPX?',f'{path.name} already exists in {folder}. Replace it?',
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:
                return
            self.route.name=name; write_gpx(self.route,path); self.remember_export(path); self.persist(); self.status.setText(f'Saved {path}')
        except Exception as exc:self.failed(str(exc))

    def closeEvent(self,event):
        self.cancel.set(); self._save_splitter(); self.persist(); event.accept()


STYLE = build_stylesheet(load_omarchy_colors())


def main():
    app=QApplication(sys.argv); app.setApplicationName('ParkLoop'); app.setOrganizationName('ParkLoop'); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    import argparse
    parser=argparse.ArgumentParser(description='ParkLoop GPX editor')
    parser.add_argument('--reference',type=Path,help='Show a GPX as a reference layer')
    parser.add_argument('--open',type=Path,help='Open a GPX for editing')
    args=parser.parse_args()
    window=Window(); window.show()
    try:
        if args.reference:
            window.map.reference=read_gpx(args.reference); window.map.fit(window.map.reference.geometry)
        if args.open:
            route=read_gpx(args.open); window.commit(route); window.map.fit(route.geometry)
    except Exception as exc:window.failed(str(exc))
    sys.exit(app.exec())


if __name__=='__main__':main()
