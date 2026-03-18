# Placeholder for main UI window

from PySide6.QtWidgets import QApplication, QMainWindow, QMenuBar, QMenu, QWidget, QVBoxLayout
from PySide6.QtGui import QAction
import sys

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenIDE - FPGA Structural Designer")
        self.resize(1000, 700)
        self._init_menu()
        self._init_toolbar()
        self._init_central_widget()

    def _init_toolbar(self):
        from PySide6.QtWidgets import QToolBar, QLabel, QToolTip
        from PySide6.QtGui import QIcon
        import os
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)
        icon_dir = os.path.join(os.path.dirname(__file__), "icons")
        actions = [
            ("Open Project", "open.png", self.load_project, "Open a project file"),
            ("Save Project", "save.png", self.save_project, "Save the current project"),
            ("Select Object", "select.png", self.select_object_mode, "Select and move wires or nodes"),
            ("Draw Wire", "wire.png", self.draw_wire_mode, "Draw a new wire"),
            ("Import VHDL Module", "import.png", self.show_add_module_dialog, "Import a VHDL module")
        ]
        for name, icon, slot, tip in actions:
            icon_path = os.path.join(icon_dir, icon)
            act = QAction(QIcon(icon_path), name, self)
            act.setToolTip(tip)
            act.triggered.connect(slot)
            toolbar.addAction(act)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        # Tooltip bubble on hover
        def show_tooltip(action):
            QToolTip.showText(self.mapToGlobal(toolbar.actionGeometry(action).center()), action.toolTip(), toolbar)
        for action in toolbar.actions():
            action.hovered.connect(lambda act=action: self._delayed_tooltip(act))
        self._tooltip_timer = None

    def _delayed_tooltip(self, action):
        from PySide6.QtCore import QTimer
        if self._tooltip_timer:
            self._tooltip_timer.stop()
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.timeout.connect(lambda: self._show_tooltip(action))
        self._tooltip_timer.start(500)

    def _show_tooltip(self, action):
        from PySide6.QtWidgets import QToolBar, QToolTip
        toolbar = self.findChild(QToolBar)
        if toolbar:
            QToolTip.showText(self.mapToGlobal(toolbar.actionGeometry(action).center()), action.toolTip(), toolbar)

    def select_object_mode(self):
        self.designer_widget.mode = "select"
    def draw_wire_mode(self):
        self.designer_widget.mode = "draw"

    def _init_menu(self):
        menubar = self.menuBar()

        # Project menu
        project_menu = menubar.addMenu("Project")
        open_action = QAction("Open Project", self)
        create_action = QAction("Create Project", self)
        options_action = QAction("Options", self)
        add_module_action = QAction("Add Module", self)
        remove_module_action = QAction("Remove Module", self)
        add_signal_action = QAction("Add Signal", self)
        save_project_action = QAction("Save Project", self)
        load_project_action = QAction("Load Project", self)
        project_menu.addAction(open_action)
        project_menu.addAction(create_action)
        project_menu.addAction(options_action)
        project_menu.addSeparator()
        project_menu.addAction(add_module_action)
        project_menu.addAction(remove_module_action)
        project_menu.addSeparator()
        project_menu.addAction(add_signal_action)
        project_menu.addSeparator()
        project_menu.addAction(save_project_action)
        project_menu.addAction(load_project_action)

        add_module_action.triggered.connect(self.show_add_module_dialog)
        remove_module_action.triggered.connect(self.show_remove_module_dialog)
        add_signal_action.triggered.connect(self.show_add_signal_dialog)
        save_project_action.triggered.connect(self.save_project)
        load_project_action.triggered.connect(self.load_project)
    def show_add_signal_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        # Select source module and port
        src_mod_names = [m['name'] for m in self.designer_widget.modules]
        if not src_mod_names:
            return
        src_mod, ok = QInputDialog.getItem(self, "Add Signal", "Source module:", src_mod_names, 0, False)
        if not ok:
            return
        src_ports = next((m['ports'] for m in self.designer_widget.modules if m['name'] == src_mod), [])
        src_port, ok = QInputDialog.getItem(self, "Add Signal", "Source port:", src_ports, 0, False)
        if not ok:
            return
        # Select destination module and port
        dst_mod_names = [m['name'] for m in self.designer_widget.modules if m['name'] != src_mod]
        if not dst_mod_names:
            return
        dst_mod, ok = QInputDialog.getItem(self, "Add Signal", "Destination module:", dst_mod_names, 0, False)
        if not ok:
            return
        dst_ports = next((m['ports'] for m in self.designer_widget.modules if m['name'] == dst_mod), [])
        dst_port, ok = QInputDialog.getItem(self, "Add Signal", "Destination port:", dst_ports, 0, False)
        if not ok:
            return
        # Add signal to designer
        self.designer_widget.signals.append({'src_mod': src_mod, 'src_port': src_port, 'dst_mod': dst_mod, 'dst_port': dst_port})
        self.designer_widget.update()

    def save_project(self):
        from PySide6.QtWidgets import QFileDialog
        from data.data_manager import DataManager
        filename, _ = QFileDialog.getSaveFileName(self, "Save Project", "", "JSON Files (*.json)")
        if filename:
            data = {
                'modules': self.designer_widget.modules,
                'signals': self.designer_widget.signals
            }
            DataManager().save_project(data, filename)

    def load_project(self):
        from PySide6.QtWidgets import QFileDialog
        from data.data_manager import DataManager
        filename, _ = QFileDialog.getOpenFileName(self, "Load Project", "", "JSON Files (*.json)")
        if filename:
            data = DataManager().load_project(filename)
            self.designer_widget.modules = data.get('modules', [])
            self.designer_widget.signals = data.get('signals', [])
            self.designer_widget.update()

        # Designer menu
        designer_menu = menubar.addMenu("Designer")
        workspace_action = QAction("Structural Workspace", self)
        designer_menu.addAction(workspace_action)

        # Additional menu (empty for future)
        menubar.addMenu("Future")

    def show_add_module_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add Module", "Module name:")
        if ok and name:
            ports, ok_ports = QInputDialog.getText(self, "Add Module", "Ports (comma separated):")
            if ok_ports:
                port_list = [p.strip() for p in ports.split(",") if p.strip()]
                # Add module to designer
                self.designer_widget.modules.append({'name': name, 'ports': port_list})
                self.designer_widget.update()

    def show_remove_module_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        names = [m['name'] for m in self.designer_widget.modules]
        if not names:
            return
        name, ok = QInputDialog.getItem(self, "Remove Module", "Select module:", names, 0, False)
        if ok and name:
            self.designer_widget.modules = [m for m in self.designer_widget.modules if m['name'] != name]
            self.designer_widget.update()

    def _init_central_widget(self):
        from designer.designer import DesignerWidget
        central = QWidget()
        layout = QVBoxLayout()
        self.designer_widget = DesignerWidget()
        layout.addWidget(self.designer_widget)
        central.setLayout(layout)
        self.setCentralWidget(central)

    def run(self):
        app = QApplication(sys.argv)
        self.show()
        app.exec()
