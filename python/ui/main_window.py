from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QToolBar, QToolTip, QToolButton,
    QFileDialog, QInputDialog, QMessageBox,
)
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtCore import Qt, QSize, QTimer, QPoint, QEvent
from data.keybindings import KeyBindings
from ui.toast import ToastNotification
import copy
import os

# Path for persisted keybindings (next to the executable / main.py)
_KEYBINDINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "keybindings.json",
)


class MainWindow(QMainWindow):
    def __init__(self, logger=None):
        super().__init__()
        self._logger = logger
        self.setWindowTitle("OpenIDE - FPGA Structural Designer")
        self.resize(1000, 700)
        self._last_import_dir = os.getcwd()
        self._project_filepath = None  # Known save path (None = never saved)

        # Tooltip hover state — a single reusable timer for the whole toolbar
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.timeout.connect(self._show_pending_tooltip)
        self._pending_tooltip_action = None

        self._init_menu()
        self._init_toolbar()
        self._init_central_widget()
        self._init_keybindings()
        self._toast = ToastNotification(self)
        self._log("MainWindow initialized")

    def _log(self, action, details=None):
        if self._logger:
            self._logger.log_action(action, details)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    # Display size for toolbar icons (the source PNGs are 384×384).
    _TOOLBAR_ICON_SIZE = 42

    def _init_toolbar(self):
        """Build the main toolbar with PNG icons.

        Each entry maps a human-readable name to its icon file, slot,
        and tooltip text.  Icons live in ui/icons/ as 384×384 PNGs and
        are scaled down to _TOOLBAR_ICON_SIZE by Qt.
        """
        toolbar = QToolBar("Main Toolbar")
        toolbar.setIconSize(QSize(self._TOOLBAR_ICON_SIZE, self._TOOLBAR_ICON_SIZE))
        toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(toolbar)

        icon_dir = os.path.join(os.path.dirname(__file__), "icons")

        # Pre-load the two save-icon variants (green = saved, orange = unsaved)
        self._save_icon_green = QIcon(os.path.join(icon_dir, "save_green_icon.png"))
        self._save_icon_orange = QIcon(os.path.join(icon_dir, "save_orange_icon.png"))

        actions = [
            ("Open Project",       "open_project_icon.png",  self.load_project,          "Open a project file"),
            ("Save Project",       None,                     self.save_project,           "Save the current project"),
            ("Select Object",      "select_icon.png",        self.select_object_mode,     "Select and move wires or nodes"),
            ("Draw Wire",          "draw_wire_icon.png",     self.draw_wire_mode,         "Draw a new wire"),
            ("Import VHDL Module", "import_vhdl_icon.png",   self.show_add_module_dialog, "Import a VHDL module"),
            ("Optimal Recenter",   "recenter_icon.png",      self.optimal_recenter,       "Fit all modules and wires in view"),
        ]
        for name, icon_file, slot, tip in actions:
            if icon_file is None:
                # Save button — starts with the orange (unsaved) icon
                act = QAction(self._save_icon_orange, name, self)
                self._save_action = act
            else:
                icon_path = os.path.join(icon_dir, icon_file)
                act = QAction(QIcon(icon_path), name, self)
            # Store the short name as tooltip text in the action's data
            # property.  We show it ourselves after a 0.5 s delay.
            # setToolTip("") prevents Qt from auto-showing the action text.
            act.setData(name)
            act.setToolTip("")
            act.triggered.connect(slot)
            toolbar.addAction(act)

        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        # Block Qt's built-in instant tooltips on every toolbar button.
        # Qt re-generates tooltips from the action text internally, so
        # clearing them is not enough — we install an event filter that
        # swallows ToolTip events.  Our custom 0.5 s delayed tooltip
        # (shown via QToolTip.showText) bypasses this filter.
        for btn in toolbar.findChildren(QToolButton):
            btn.installEventFilter(self)

        # Add spacing between buttons
        toolbar.setStyleSheet("QToolButton { margin: 0 4px; }")

        # Connect hover signal for the delayed tooltip
        for action in toolbar.actions():
            action.hovered.connect(lambda a=action: self._on_toolbar_hover(a))

    # ------------------------------------------------------------------
    # Delayed toolbar tooltip (0.5 s hover)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        """Suppress Qt's built-in instant tooltips on toolbar buttons.

        Our custom tooltip is shown via _show_pending_tooltip after 0.5 s,
        using QToolTip.showText — that path does not trigger this filter.
        """
        if event.type() == QEvent.ToolTip and isinstance(obj, QToolButton):
            return True  # swallow the event
        return super().eventFilter(obj, event)

    def _on_toolbar_hover(self, action):
        """Start (or restart) the 0.5 s timer when the cursor enters a button."""
        self._pending_tooltip_action = action
        self._tooltip_timer.start(500)

    def _show_pending_tooltip(self):
        """Show the tooltip for the action the cursor was last hovering over."""
        action = self._pending_tooltip_action
        if action is None:
            return
        toolbar = self.findChild(QToolBar)
        if toolbar is None:
            return
        tip = action.data()  # tooltip text stored in action data
        if not tip:
            return
        # Position the tooltip at the center of the toolbar button
        btn_rect = toolbar.actionGeometry(action)
        global_pos = toolbar.mapToGlobal(btn_rect.center())
        QToolTip.showText(global_pos, tip, toolbar)

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def select_object_mode(self):
        self.designer_widget.mode = "select"

    def draw_wire_mode(self):
        self.designer_widget.mode = "draw"

    def optimal_recenter(self):
        self.designer_widget.optimal_recenter()

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _init_menu(self):
        menubar = self.menuBar()

        # --- Project menu ---
        project_menu = menubar.addMenu("Project")

        open_action = QAction("Open Project", self)
        open_action.triggered.connect(self.load_project)
        project_menu.addAction(open_action)

        create_action = QAction("Create Project", self)
        project_menu.addAction(create_action)

        options_action = QAction("Options", self)
        options_action.triggered.connect(self._open_options_dialog)
        project_menu.addAction(options_action)

        project_menu.addSeparator()

        add_module_action = QAction("Add Module", self)
        add_module_action.triggered.connect(self.show_add_module_dialog)
        project_menu.addAction(add_module_action)

        remove_module_action = QAction("Remove Module", self)
        remove_module_action.triggered.connect(self.show_remove_module_dialog)
        project_menu.addAction(remove_module_action)

        project_menu.addSeparator()

        add_signal_action = QAction("Add Signal", self)
        add_signal_action.triggered.connect(self.show_add_signal_dialog)
        project_menu.addAction(add_signal_action)

        project_menu.addSeparator()

        save_project_action = QAction("Save Project", self)
        save_project_action.triggered.connect(self.save_project)
        project_menu.addAction(save_project_action)

        load_project_action = QAction("Load Project", self)
        load_project_action.triggered.connect(self.load_project)
        project_menu.addAction(load_project_action)

        # --- Designer menu ---
        designer_menu = menubar.addMenu("Designer")
        workspace_action = QAction("Structural Workspace", self)
        designer_menu.addAction(workspace_action)

        # --- Future menu (placeholder) ---
        menubar.addMenu("Future")

    # ------------------------------------------------------------------
    # Central widget
    # ------------------------------------------------------------------

    def _init_central_widget(self):
        from designer.designer import DesignerWidget

        central = QWidget()
        layout = QVBoxLayout()
        self.designer_widget = DesignerWidget()
        layout.addWidget(self.designer_widget)
        central.setLayout(layout)
        self.setCentralWidget(central)

        # Wire up the design-changed callback so we can update the save icon
        self.designer_widget.on_design_changed = self._on_design_changed

        # Snapshot representing the last-saved state.  None = never saved.
        self._saved_state = None

    # ------------------------------------------------------------------
    # Save-icon state tracking
    # ------------------------------------------------------------------

    def _on_design_changed(self):
        """Called by DesignerWidget whenever the design state mutates.

        Compares the current design to the last-saved snapshot and swaps
        the save-button icon between green (clean) and orange (dirty).
        """
        self._refresh_save_icon()

    def _refresh_save_icon(self):
        """Set the save icon to green if the design matches the saved state,
        orange otherwise."""
        if self._saved_state is not None:
            saved_mods, saved_sigs = self._saved_state
            if (self.designer_widget.modules == saved_mods
                    and self.designer_widget.signals == saved_sigs):
                self._save_action.setIcon(self._save_icon_green)
                return
        self._save_action.setIcon(self._save_icon_orange)

    # ------------------------------------------------------------------
    # Keybindings
    # ------------------------------------------------------------------

    def _init_keybindings(self):
        """Load keybindings from disk and wire them into the designer."""
        self._keybindings = KeyBindings.load(_KEYBINDINGS_PATH)
        self.designer_widget.keybindings = self._keybindings
        self.designer_widget._save_callback = self.save_project

    def _open_options_dialog(self):
        """Open the keyboard-shortcuts editor."""
        from ui.options_dialog import KeybindingsDialog

        dlg = KeybindingsDialog(self._keybindings, parent=self)
        if dlg.exec() and dlg.changed:
            self._keybindings.save(_KEYBINDINGS_PATH)
            self.designer_widget.keybindings = self._keybindings
            self._log("keybindings_saved", _KEYBINDINGS_PATH)

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def save_project(self):
        """Save the project.

        If the project file path is already known, save directly.
        Otherwise, open a file dialog so the user can choose a location.
        After a successful save, show a brief "Saved" toast notification.
        """
        from data.data_manager import DataManager

        # Determine target file path
        filepath = self._project_filepath
        if filepath is None:
            filepath, _ = QFileDialog.getSaveFileName(
                self, "Save Project", "", "JSON Files (*.json)"
            )
            if not filepath:
                return  # User cancelled

        data = {
            'modules': self.designer_widget.modules,
            'signals': self.designer_widget.signals,
        }
        DataManager(logger=self._logger).save_project(data, filepath)
        self._project_filepath = filepath

        # Record the saved state so the icon can track dirty/clean
        self._saved_state = (
            copy.deepcopy(self.designer_widget.modules),
            copy.deepcopy(self.designer_widget.signals),
        )
        self._refresh_save_icon()
        self._toast.show_message("Saved", style="success")

    def load_project(self):
        from data.data_manager import DataManager

        filename, _ = QFileDialog.getOpenFileName(
            self, "Load Project", "", "JSON Files (*.json)"
        )
        if filename:
            data = DataManager(logger=self._logger).load_project(filename)
            self.designer_widget.modules = data.get('modules', [])
            self.designer_widget.signals = data.get('signals', [])

            # Ensure every port has an explicit grid position (backward compat)
            for mod in self.designer_widget.modules:
                self.designer_widget._ensure_port_positions(mod)

            self.designer_widget.update()
            # Remember the path so subsequent saves go to the same file
            self._project_filepath = filename
            # Treat the loaded state as the "saved" baseline
            self._saved_state = (
                copy.deepcopy(self.designer_widget.modules),
                copy.deepcopy(self.designer_widget.signals),
            )
            self._refresh_save_icon()

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    def show_add_module_dialog(self):
        from modules.vhdl_parser import parse_vhdl_file
        from designer.designer import DEFAULT_MODULE_W, DEFAULT_MODULE_H, DEFAULT_MODULE_COLOR

        filepaths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import VHDL Module(s)",
            self._last_import_dir,
            "VHDL Files (*.vhd *.vhdl);;All Files (*)",
        )
        if not filepaths:
            return

        # Remember the directory of the first selected file
        self._last_import_dir = os.path.dirname(filepaths[0])
        self._log("import_vhdl_browse", f"dir={self._last_import_dir} files={len(filepaths)}")

        for fpath in filepaths:
            parsed = parse_vhdl_file(fpath)
            if parsed is None:
                QMessageBox.warning(
                    self, "Import Error",
                    f"Could not parse entity from:\n{os.path.basename(fpath)}",
                )
                self._log("import_vhdl_fail", fpath)
                continue

            instance_name = self.designer_widget.next_instance_name()
            custom_name, ok = QInputDialog.getText(
                self, "Import VHDL Module",
                f"Instance name for '{parsed['entity']}' (default: {instance_name}):",
            )
            if not ok:
                continue
            instance_name = custom_name.strip() if custom_name.strip() else instance_name

            # Ensure every port carries a label_offset for movable port names
            ports = parsed['ports']
            for p in ports:
                p.setdefault('label_offset', [0, 0])

            # Build the module dict so we can compute the minimum size
            # required to fit all ports without overlap.
            new_mod = {
                'name': instance_name,
                'entity': parsed['entity'],
                'library': parsed['library'],
                'ports': ports,
                'x': 100 + len(self.designer_widget.modules) * 400,
                'y': 100,
                'width': DEFAULT_MODULE_W,
                'height': DEFAULT_MODULE_H,
                'color': list(DEFAULT_MODULE_COLOR),
                'name_offset': [0, 0],
                'entity_offset': [0, 0],
            }
            min_w, min_h = self.designer_widget._min_module_size(new_mod)
            new_mod['width'] = max(DEFAULT_MODULE_W, min_w)
            new_mod['height'] = max(DEFAULT_MODULE_H, min_h)

            # Assign explicit grid positions to each port
            self.designer_widget._ensure_port_positions(new_mod)

            # Snapshot BEFORE adding the module so CTRL+Z can revert it
            self.designer_widget._save_undo()
            self.designer_widget.modules.append(new_mod)
            self.designer_widget._notify_design_changed()
            self._log("import_vhdl_ok", f"{instance_name} entity={parsed['entity']} ports={len(ports)}")

        self.designer_widget.update()

    def show_remove_module_dialog(self):
        names = [m['name'] for m in self.designer_widget.modules]
        if not names:
            return
        name, ok = QInputDialog.getItem(
            self, "Remove Module", "Select module:", names, 0, False
        )
        if ok and name:
            # Snapshot BEFORE removing so CTRL+Z can revert it
            self.designer_widget._save_undo()
            self.designer_widget.modules = [
                m for m in self.designer_widget.modules if m['name'] != name
            ]
            self.designer_widget._notify_design_changed()
            self.designer_widget.update()

    def show_add_signal_dialog(self):
        src_mod_names = [m['name'] for m in self.designer_widget.modules]
        if not src_mod_names:
            return
        src_mod, ok = QInputDialog.getItem(
            self, "Add Signal", "Source module:", src_mod_names, 0, False
        )
        if not ok:
            return
        src_ports = next(
            (m['ports'] for m in self.designer_widget.modules if m['name'] == src_mod), []
        )
        src_port, ok = QInputDialog.getItem(
            self, "Add Signal", "Source port:", src_ports, 0, False
        )
        if not ok:
            return
        dst_mod_names = [
            m['name'] for m in self.designer_widget.modules if m['name'] != src_mod
        ]
        if not dst_mod_names:
            return
        dst_mod, ok = QInputDialog.getItem(
            self, "Add Signal", "Destination module:", dst_mod_names, 0, False
        )
        if not ok:
            return
        dst_ports = next(
            (m['ports'] for m in self.designer_widget.modules if m['name'] == dst_mod), []
        )
        dst_port, ok = QInputDialog.getItem(
            self, "Add Signal", "Destination port:", dst_ports, 0, False
        )
        if not ok:
            return
        # Snapshot BEFORE adding the signal so CTRL+Z can revert it
        self.designer_widget._save_undo()
        self.designer_widget.signals.append({
            'src_mod': src_mod,
            'src_port': src_port,
            'dst_mod': dst_mod,
            'dst_port': dst_port,
        })
        self.designer_widget._notify_design_changed()
        self.designer_widget.update()
