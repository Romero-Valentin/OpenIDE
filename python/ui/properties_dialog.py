"""Properties dialog for editing VHDL block and wire attributes.

Displays a context-sensitive panel depending on the selected object(s):

- **Single module**: instance name + fill colour (10 presets + custom RGB).
- **Single wire**: signal name + wire colour (10 presets + custom RGB).
- **Multiple objects**: only the *common* editable properties are shown.
  Both modules and wires have a colour, so when a mixed selection exists
  the colour picker is displayed.  Name fields are hidden for multi-select
  because each object's name is unique.

All property changes are applied to every selected object at once and
recorded as a single undoable action.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QGridLayout, QColorDialog,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

# 10 preset colours available for blocks and wires.
# Each entry: (display name, RGB tuple).
COLOUR_PRESETS: list[tuple[str, tuple[int, int, int]]] = [
    ("Green",  (0, 200, 0)),
    ("Red",    (220, 40, 40)),
    ("Blue",   (50, 100, 255)),
    ("Cyan",   (0, 210, 210)),
    ("Purple", (160, 50, 200)),
    ("White",  (245, 245, 245)),
    ("Grey",   (160, 160, 160)),
    ("Yellow", (240, 220, 30)),
    ("Orange", (240, 150, 30)),
    ("Wood",   (180, 120, 60)),
]


class _ColourPicker(QGroupBox):
    """Reusable colour-picker widget: preset grid + custom RGB button."""

    def __init__(self, label: str, initial: list[int] | tuple[int, int, int],
                 parent=None):
        super().__init__(label, parent)
        self._colour = list(initial)

        layout = QVBoxLayout(self)

        # --- Preset grid (2 rows × 5 columns) ---
        grid = QGridLayout()
        grid.setSpacing(4)
        for i, (name, rgb) in enumerate(COLOUR_PRESETS):
            btn = QPushButton()
            btn.setFixedSize(40, 28)
            btn.setToolTip(name)
            btn.setStyleSheet(
                f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]});"
                "border: 1px solid #555; border-radius: 3px;"
            )
            btn.clicked.connect(lambda checked=False, c=rgb: self._set_colour(c))
            grid.addWidget(btn, i // 5, i % 5)
        layout.addLayout(grid)

        # --- Current colour preview + custom button ---
        row = QHBoxLayout()
        self._preview = QLabel()
        self._preview.setFixedSize(60, 28)
        self._update_preview()
        row.addWidget(self._preview)

        custom_btn = QPushButton("Custom…")
        custom_btn.clicked.connect(self._pick_custom)
        row.addWidget(custom_btn)
        row.addStretch()
        layout.addLayout(row)

    # --- public API ----------------------------------------------------------

    @property
    def colour(self) -> list[int]:
        """Return the currently selected colour as [R, G, B]."""
        return list(self._colour)

    # --- internals -----------------------------------------------------------

    def _set_colour(self, rgb):
        self._colour = list(rgb)
        self._update_preview()

    def _pick_custom(self):
        """Open the system colour dialog for an arbitrary RGB value."""
        initial = QColor(*self._colour)
        chosen = QColorDialog.getColor(initial, self, "Choose Colour")
        if chosen.isValid():
            self._set_colour((chosen.red(), chosen.green(), chosen.blue()))

    def _update_preview(self):
        r, g, b = self._colour
        self._preview.setStyleSheet(
            f"background-color: rgb({r},{g},{b});"
            "border: 1px solid #555; border-radius: 3px;"
        )


# ---------------------------------------------------------------------------
# Module properties dialog
# ---------------------------------------------------------------------------

class ModulePropertiesDialog(QDialog):
    """Edit properties of a single VHDL block (module).

    Fields:
    - Instance name (editable text).
    - Fill colour (preset grid + custom RGB picker).
    """

    def __init__(self, name: str, colour: list[int], *, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Module Properties")
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)

        # --- Instance name ---
        name_group = QGroupBox("Instance Name")
        name_lay = QHBoxLayout(name_group)
        self._name_edit = QLineEdit(name)
        name_lay.addWidget(self._name_edit)
        layout.addWidget(name_group)

        # --- Colour ---
        self._colour_picker = _ColourPicker("Fill Colour", colour)
        layout.addWidget(self._colour_picker)

        # --- OK / Cancel ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # --- public read-back ----------------------------------------------------

    @property
    def module_name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def colour(self) -> list[int]:
        return self._colour_picker.colour


# ---------------------------------------------------------------------------
# Wire properties dialog
# ---------------------------------------------------------------------------

class WirePropertiesDialog(QDialog):
    """Edit properties of a single wire / signal.

    Fields:
    - Signal name (editable text, displayed on the canvas).
    - Wire colour (preset grid + custom RGB picker).
    """

    def __init__(self, name: str, colour: list[int], *, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wire Properties")
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)

        # --- Signal name ---
        name_group = QGroupBox("Signal Name")
        name_lay = QHBoxLayout(name_group)
        self._name_edit = QLineEdit(name)
        name_lay.addWidget(self._name_edit)
        layout.addWidget(name_group)

        # --- Colour ---
        self._colour_picker = _ColourPicker("Wire Colour", colour)
        layout.addWidget(self._colour_picker)

        # --- OK / Cancel ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # --- public read-back ----------------------------------------------------

    @property
    def signal_name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def colour(self) -> list[int]:
        return self._colour_picker.colour


# ---------------------------------------------------------------------------
# Multi-selection properties dialog
# ---------------------------------------------------------------------------

class MultiPropertiesDialog(QDialog):
    """Edit common properties of a heterogeneous selection.

    The dialog inspects the selected modules and wires and shows only
    the properties that *all* selected objects share:

    - **Colour** is always shown (both modules and wires have colour).
    - **Instance name** is shown only when exactly one module is selected
      and no wires are selected.
    - **Signal name** is shown only when exactly one wire is selected
      and no modules are selected.

    Parameters
    ----------
    modules : list[dict]
        The selected module dicts (mutable references).
    wires : list[dict]
        The selected wire dicts (mutable references).
    default_colour : list[int]
        Initial colour shown in the picker.  When objects share the same
        colour it is that colour; otherwise the first object's colour.
    """

    def __init__(self, modules: list[dict], wires: list[dict],
                 default_colour: list[int], *, parent=None):
        super().__init__(parent)
        n_mods = len(modules)
        n_wires = len(wires)
        total = n_mods + n_wires
        self.setWindowTitle(f"Properties ({total} object{'s' if total > 1 else ''})")
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)

        # --- Instance name (single module only) ---
        self._name_edit: QLineEdit | None = None
        if n_mods == 1 and n_wires == 0:
            name_group = QGroupBox("Instance Name")
            name_lay = QHBoxLayout(name_group)
            self._name_edit = QLineEdit(modules[0].get('name', ''))
            name_lay.addWidget(self._name_edit)
            layout.addWidget(name_group)

        # --- Signal name (single wire only) ---
        self._signal_edit: QLineEdit | None = None
        if n_wires == 1 and n_mods == 0:
            sig_group = QGroupBox("Signal Name")
            sig_lay = QHBoxLayout(sig_group)
            self._signal_edit = QLineEdit(wires[0].get('name', ''))
            sig_lay.addWidget(self._signal_edit)
            layout.addWidget(sig_group)

        # --- Colour (always shown — common to both types) ---
        self._colour_picker = _ColourPicker("Colour", default_colour)
        layout.addWidget(self._colour_picker)

        # --- OK / Cancel ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    # --- public read-back ----------------------------------------------------

    @property
    def module_name(self) -> str | None:
        """Instance name if the field was shown, else None."""
        return self._name_edit.text().strip() if self._name_edit else None

    @property
    def signal_name(self) -> str | None:
        """Signal name if the field was shown, else None."""
        return self._signal_edit.text().strip() if self._signal_edit else None

    @property
    def colour(self) -> list[int]:
        return self._colour_picker.colour
