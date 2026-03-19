from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QToolBar, QToolTip,
    QFileDialog, QInputDialog, QMessageBox,
)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import QTimer
import os


class MainWindow(QMainWindow):
    def __init__(self, logger=None):
        super().__init__()
        self._logger = logger
        self.setWindowTitle("OpenIDE - FPGA Structural Designer")
        self.resize(1000, 700)
        self._tooltip_timer = None
        self._last_import_dir = os.getcwd()
        self._init_menu()
        self._init_toolbar()
        self._init_central_widget()
        self._log("MainWindow initialized")

    def _log(self, action, details=None):
        if self._logger:
            self._logger.log_action(action, details)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _init_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)
        icon_dir = os.path.join(os.path.dirname(__file__), "icons")
        actions = [
            ("Open Project", "open.png", self.load_project, "Open a project file"),
            ("Save Project", "save.png", self.save_project, "Save the current project"),
            ("Select Object", "select.png", self.select_object_mode, "Select and move wires or nodes"),
            ("Draw Wire", "wire.png", self.draw_wire_mode, "Draw a new wire"),
            ("Import VHDL Module", "import.png", self.show_add_module_dialog, "Import a VHDL module"),
            ("Optimal Recenter", "recenter.png", self.optimal_recenter, "Fit all modules and wires in view"),
        ]
        for name, icon, slot, tip in actions:
            icon_path = os.path.join(icon_dir, icon)
            act = QAction(QIcon(icon_path), name, self)
            act.setToolTip(tip)
            act.triggered.connect(slot)
            toolbar.addAction(act)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        for action in toolbar.actions():
            action.hovered.connect(lambda act=action: self._delayed_tooltip(act))

    def _delayed_tooltip(self, action):
        if self._tooltip_timer:
            self._tooltip_timer.stop()
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.timeout.connect(lambda: self._show_tooltip(action))
        self._tooltip_timer.start(500)

    def _show_tooltip(self, action):
        toolbar = self.findChild(QToolBar)
        if toolbar:
            QToolTip.showText(
                self.mapToGlobal(toolbar.actionGeometry(action).center()),
                action.toolTip(),
                toolbar,
            )

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

    # ------------------------------------------------------------------
    # Save / Load
    # ------------------------------------------------------------------

    def save_project(self):
        from data.data_manager import DataManager

        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Project", "", "JSON Files (*.json)"
        )
        if filename:
            data = {
                'modules': self.designer_widget.modules,
                'signals': self.designer_widget.signals,
            }
            DataManager(logger=self._logger).save_project(data, filename)

    def load_project(self):
        from data.data_manager import DataManager

        filename, _ = QFileDialog.getOpenFileName(
            self, "Load Project", "", "JSON Files (*.json)"
        )
        if filename:
            data = DataManager(logger=self._logger).load_project(filename)
            self.designer_widget.modules = data.get('modules', [])
            self.designer_widget.signals = data.get('signals', [])
            self.designer_widget.update()

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    def show_add_module_dialog(self):
        from modules.vhdl_parser import parse_vhdl_file

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

            count = len(self.designer_widget.modules)
            self.designer_widget.modules.append({
                'name': instance_name,
                'entity': parsed['entity'],
                'library': parsed['library'],
                'ports': parsed['ports'],
                'x': 100 + count * 200,
                'y': 100,
            })
            self._log("import_vhdl_ok", f"{instance_name} entity={parsed['entity']} ports={len(parsed['ports'])}")

        self.designer_widget.update()

    def show_remove_module_dialog(self):
        names = [m['name'] for m in self.designer_widget.modules]
        if not names:
            return
        name, ok = QInputDialog.getItem(
            self, "Remove Module", "Select module:", names, 0, False
        )
        if ok and name:
            self.designer_widget.modules = [
                m for m in self.designer_widget.modules if m['name'] != name
            ]
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
        self.designer_widget.signals.append({
            'src_mod': src_mod,
            'src_port': src_port,
            'dst_mod': dst_mod,
            'dst_port': dst_port,
        })
        self.designer_widget.update()
