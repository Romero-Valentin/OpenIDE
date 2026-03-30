"""Options dialog for application settings and keyboard shortcuts.

The dialog contains two sections:

1. **Performance** — toggleable performance overlay.
2. **Keyboard Shortcuts** — table of all registered actions with
   click-to-capture reassignment and conflict detection.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QLabel, QMessageBox, QAbstractItemView,
    QCheckBox, QGroupBox, QComboBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent

from data.keybindings import (
    KeyBindings, KeyCombo, qt_key_to_name, modifiers_to_strings,
)


# -----------------------------------------------------------------------
# Pan tick-rate presets
# -----------------------------------------------------------------------

# Each entry is (label, hz_value).  hz_value == 0 means VSync mode.
_PAN_HZ_PRESETS: list[tuple[str, int]] = [
    ("VSync — match display (default)", 0),
    ("240 Hz", 240),
    ("120 Hz", 120),
    ("60 Hz", 60),
]


# -----------------------------------------------------------------------
# Human-readable labels for action names
# -----------------------------------------------------------------------

_ACTION_LABELS: dict[str, str] = {
    "pan_left":  "Pan left",
    "pan_right": "Pan right",
    "pan_up":    "Pan up",
    "pan_down":  "Pan down",
    "undo":      "Undo",
    "redo":      "Redo",
    "save":      "Save project",
    "copy":      "Copy selected",
    "paste":     "Paste",
    "delete":    "Delete selected",
    "cancel":    "Cancel action",
}


def _label_for(action: str) -> str:
    return _ACTION_LABELS.get(action, action)


# -----------------------------------------------------------------------
# Combo editor cell — captures key-presses to record a new combo
# -----------------------------------------------------------------------

class _ComboCaptureWidget(QLabel):
    """A label that captures the next key-press and emits *combo_set*."""

    combo_set = Signal(object)  # emits a KeyCombo

    def __init__(self, combo: KeyCombo | None = None, parent=None):
        super().__init__(parent)
        self._combo = combo
        self._capturing = False
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("padding: 4px;")
        self._refresh_text()

    def _refresh_text(self):
        if self._capturing:
            self.setText("Press a key…")
            self.setStyleSheet("padding: 4px; background: #fffbe6; border: 1px solid #e6c300;")
        elif self._combo:
            self.setText(self._combo.display())
            self.setStyleSheet("padding: 4px;")
        else:
            self.setText("(none)")
            self.setStyleSheet("padding: 4px; color: #888;")

    @property
    def combo(self) -> KeyCombo | None:
        return self._combo

    def start_capture(self):
        self._capturing = True
        self._refresh_text()
        self.setFocus(Qt.OtherFocusReason)

    def keyPressEvent(self, event: QKeyEvent):
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        # Ignore bare modifier presses (user hasn't finished the combo yet)
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        name = qt_key_to_name(key)
        if name is None:
            return
        mods = frozenset(modifiers_to_strings(event.modifiers()))
        self._combo = KeyCombo(name, mods)
        self._capturing = False
        self._refresh_text()
        self.combo_set.emit(self._combo)

    def focusOutEvent(self, event):
        if self._capturing:
            self._capturing = False
            self._refresh_text()
        super().focusOutEvent(event)


# -----------------------------------------------------------------------
# Main options dialog
# -----------------------------------------------------------------------

class OptionsDialog(QDialog):
    """Modal dialog for application settings and keyboard shortcuts."""

    def __init__(self, keybindings: KeyBindings, *,
                 show_fps: bool = False,
                 show_grid: bool = True,
                 pan_hz: int = 120,
                 pan_vsync: bool = False,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumSize(540, 480)
        self._kb = keybindings
        self._changed = False

        layout = QVBoxLayout(self)

        # --- Display section -----------------------------------------------
        display_group = QGroupBox("Display")
        display_layout = QVBoxLayout(display_group)
        self._grid_checkbox = QCheckBox("Show grid dots")
        self._grid_checkbox.setChecked(show_grid)
        display_layout.addWidget(self._grid_checkbox)
        layout.addWidget(display_group)

        # --- Performance section -------------------------------------------
        perf_group = QGroupBox("Performance")
        perf_layout = QVBoxLayout(perf_group)
        self._fps_checkbox = QCheckBox(
            "Show performance overlay (bottom-right corner)")
        self._fps_checkbox.setChecked(show_fps)
        perf_layout.addWidget(self._fps_checkbox)

        # Pan tick-rate selector
        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Pan tick rate:"))
        self._pan_hz_combo = QComboBox()
        selected_index = 2  # default to "120 Hz"
        for idx, (label, hz) in enumerate(_PAN_HZ_PRESETS):
            self._pan_hz_combo.addItem(label, userData=hz)
            if pan_vsync and hz == 0:
                selected_index = idx
            elif not pan_vsync and hz == pan_hz:
                selected_index = idx
        self._pan_hz_combo.setCurrentIndex(selected_index)
        rate_row.addWidget(self._pan_hz_combo)
        rate_row.addStretch()
        perf_layout.addLayout(rate_row)

        layout.addWidget(perf_group)

        # --- Keyboard Shortcuts section ------------------------------------
        kb_group = QGroupBox("Keyboard Shortcuts")
        kb_layout = QVBoxLayout(kb_group)

        hint = QLabel(
            "Click a shortcut cell, then press a new key combination "
            "to reassign it.")
        hint.setWordWrap(True)
        kb_layout.addWidget(hint)

        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Action", "Shortcuts"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        kb_layout.addWidget(self._table)

        self._populate()

        kb_btn_layout = QHBoxLayout()
        self._btn_reset = QPushButton("Reset Defaults")
        self._btn_reset.clicked.connect(self._reset_defaults)
        kb_btn_layout.addWidget(self._btn_reset)
        kb_btn_layout.addStretch()
        kb_layout.addLayout(kb_btn_layout)

        layout.addWidget(kb_group)

        # --- Dialog buttons ------------------------------------------------
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._btn_ok = QPushButton("OK")
        self._btn_ok.clicked.connect(self.accept)
        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self._btn_ok)
        btn_layout.addWidget(self._btn_cancel)
        layout.addLayout(btn_layout)

        self._table.cellClicked.connect(self._on_cell_clicked)

    # ----- table build -----------------------------------------------------

    def _populate(self):
        actions = self._kb.all_actions()
        self._table.setRowCount(len(actions))
        self._actions = actions
        for row, action in enumerate(actions):
            # Column 0: action label  (read-only)
            item = QTableWidgetItem(_label_for(action))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(row, 0, item)
            # Column 1: shortcuts display
            combos = self._kb.keys_for_action(action)
            display = ", ".join(c.display() for c in combos) if combos else "(none)"
            shortcut_item = QTableWidgetItem(display)
            shortcut_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(row, 1, shortcut_item)

    # ----- interaction -----------------------------------------------------

    def _on_cell_clicked(self, row: int, col: int):
        """Start capturing a new combo for the clicked action."""
        if col != 1:
            return
        action = self._actions[row]
        capture = _ComboCaptureWidget(parent=self)
        capture.combo_set.connect(lambda combo, a=action, r=row: self._on_combo_captured(a, r, combo))
        capture.start_capture()
        self._table.setCellWidget(row, 1, capture)

    def _on_combo_captured(self, action: str, row: int, combo: KeyCombo):
        """Handle a newly captured combo."""
        # Check for conflicts across other actions
        for other_action in self._actions:
            if other_action == action:
                continue
            for existing in self._kb.keys_for_action(other_action):
                if existing == combo:
                    reply = QMessageBox.question(
                        self, "Shortcut Conflict",
                        f'"{combo.display()}" is already used by '
                        f'"{_label_for(other_action)}".\n\n'
                        f"Remove it from {_label_for(other_action)} and assign here?",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if reply == QMessageBox.No:
                        self._refresh_row(row, action)
                        return
                    # Remove conflicting combo from the other action
                    other_combos = [c for c in self._kb.keys_for_action(other_action) if c != combo]
                    self._kb.set_binding(other_action, other_combos)
                    other_row = self._actions.index(other_action)
                    self._refresh_row(other_row, other_action)

        # Add the new combo (append to existing list)
        current = self._kb.keys_for_action(action)
        if combo not in current:
            current.append(combo)
        self._kb.set_binding(action, current)
        self._changed = True
        self._refresh_row(row, action)

    def _refresh_row(self, row: int, action: str):
        """Refresh the display cell for an action after a binding change."""
        self._table.removeCellWidget(row, 1)
        combos = self._kb.keys_for_action(action)
        display = ", ".join(c.display() for c in combos) if combos else "(none)"
        item = QTableWidgetItem(display)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self._table.setItem(row, 1, item)

    def _reset_defaults(self):
        reply = QMessageBox.question(
            self, "Reset Defaults",
            "Restore all shortcuts to their default values?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._kb.reset_defaults()
            self._changed = True
            self._populate()

    # ----- result ----------------------------------------------------------

    @property
    def changed(self) -> bool:
        return self._changed

    @property
    def show_grid(self) -> bool:
        """Return the current state of the grid-visibility checkbox."""
        return self._grid_checkbox.isChecked()

    @property
    def show_fps(self) -> bool:
        """Return the current state of the FPS-overlay checkbox."""
        return self._fps_checkbox.isChecked()

    @property
    def pan_hz(self) -> int:
        """Return the selected pan tick rate in Hz (0 = VSync)."""
        return self._pan_hz_combo.currentData()

    @property
    def pan_vsync(self) -> bool:
        """Return True if VSync (display-rate matching) is selected."""
        return self._pan_hz_combo.currentData() == 0


# Backward-compatible alias
KeybindingsDialog = OptionsDialog
