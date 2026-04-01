"""Centralised, user-configurable keyboard-shortcut manager.

Every action the application can trigger via keyboard is registered
here with a unique *action name*.  Each action maps to one or more
key-combos (key + optional modifiers).  Bindings are persisted to a
JSON file so users can customise them and the changes survive restarts.

Typical usage
-------------
    kb = KeyBindings.load("keybindings.json")
    kb.action_for_event(event)      # → "undo" | "pan_left" | None
    kb.keys_for_action("undo")      # → [("Ctrl", "Z")]
    kb.set_binding("pan_left", [(None, "A"), (None, "Q"), (None, "Left")])
    kb.save("keybindings.json")
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from PySide6.QtCore import Qt


# -----------------------------------------------------------------------
# Qt helpers — translate between Qt enums and portable string names
# -----------------------------------------------------------------------

_MODIFIER_MAP: dict[str, Qt.KeyboardModifier] = {
    "Ctrl":  Qt.ControlModifier,
    "Shift": Qt.ShiftModifier,
    "Alt":   Qt.AltModifier,
}
_MODIFIER_REVERSE = {v: k for k, v in _MODIFIER_MAP.items()}

# Build key-name ↔ Qt.Key maps from Qt's own Key enum.
_KEY_NAME_TO_QT: dict[str, int] = {}
_QT_TO_KEY_NAME: dict[int, str] = {}

def _build_key_maps():
    """Populate the key-name dictionaries from the Qt.Key enum."""
    for attr in dir(Qt.Key):
        if attr.startswith("Key_"):
            pretty = attr[4:]                        # e.g. "Left", "Z", "Delete"
            qt_val = getattr(Qt.Key, attr)
            int_val = int(qt_val)
            _KEY_NAME_TO_QT[pretty] = int_val
            _QT_TO_KEY_NAME[int_val] = pretty

_build_key_maps()


def key_name_to_qt(name: str) -> int | None:
    """Return the Qt.Key constant for a portable key name, or None."""
    return _KEY_NAME_TO_QT.get(name)


def qt_key_to_name(qt_key: int) -> str | None:
    """Return the portable key name for a Qt.Key constant, or None."""
    return _QT_TO_KEY_NAME.get(qt_key)


def modifiers_to_strings(mods: Qt.KeyboardModifier) -> list[str]:
    """Return a sorted list of modifier name strings for a Qt modifier mask."""
    result = []
    for name, flag in _MODIFIER_MAP.items():
        if mods & flag:
            result.append(name)
    result.sort()
    return result


def strings_to_modifiers(names: list[str]) -> Qt.KeyboardModifier:
    """Return a Qt modifier mask from a list of modifier name strings."""
    mask = Qt.NoModifier
    for n in names:
        flag = _MODIFIER_MAP.get(n)
        if flag:
            mask |= flag
    return mask


# -----------------------------------------------------------------------
# Combo data structure
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class KeyCombo:
    """One key-press combination: a key name plus zero or more modifiers.

    *modifiers* is a frozenset of modifier strings like ``{"Ctrl"}``.
    *key* is a portable name such as ``"Z"`` or ``"Left"``.
    """
    key: str
    modifiers: frozenset[str] = field(default_factory=frozenset)

    # Serialisation helpers -------------------------------------------------

    def to_dict(self) -> dict:
        d: dict = {"key": self.key}
        if self.modifiers:
            d["modifiers"] = sorted(self.modifiers)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "KeyCombo":
        return cls(
            key=d["key"],
            modifiers=frozenset(d.get("modifiers", [])),
        )

    def display(self) -> str:
        """Human-readable label, e.g. ``Ctrl+Z``."""
        parts = sorted(self.modifiers) + [self.key]
        return "+".join(parts)

    # Qt matching -----------------------------------------------------------

    def matches_event(self, qt_key: int, qt_modifiers: Qt.KeyboardModifier) -> bool:
        """Return True if a Qt key-press event matches this combo."""
        expected_key = key_name_to_qt(self.key)
        if expected_key is None or qt_key != expected_key:
            return False
        expected_mods = strings_to_modifiers(list(self.modifiers))
        # Mask out irrelevant modifiers (Keypad, GroupSwitch, …)
        relevant = Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier
        return (qt_modifiers & relevant) == (expected_mods & relevant)


# -----------------------------------------------------------------------
# Default bindings
# -----------------------------------------------------------------------

def _defaults() -> dict[str, list[KeyCombo]]:
    """Return the factory-default action → combos mapping."""
    C = KeyCombo
    return {
        # Canvas movement
        "pan_left":   [C("Left"), C("A"), C("Q")],
        "pan_right":  [C("Right"), C("D")],
        "pan_up":     [C("Up"), C("W"), C("Z")],
        "pan_down":   [C("Down"), C("S")],
        # Jump pan (1/3 of screen)
        "jump_left":  [C("Left", frozenset({"Shift"}))],
        "jump_right": [C("Right", frozenset({"Shift"}))],
        "jump_up":    [C("Up", frozenset({"Shift"}))],
        "jump_down":  [C("Down", frozenset({"Shift"}))],
        # Editing
        "undo":       [C("Z", frozenset({"Ctrl"}))],
        "redo":       [C("E", frozenset({"Ctrl"}))],
        "save":       [C("S", frozenset({"Ctrl"}))],
        "copy":       [C("C", frozenset({"Ctrl"}))],
        "paste":      [C("V", frozenset({"Ctrl"}))],
        "delete":     [C("Delete")],
        "cancel":     [C("Escape")],
    }


# -----------------------------------------------------------------------
# KeyBindings manager
# -----------------------------------------------------------------------

class KeyBindings:
    """Configurable action ↔ key-combo registry with JSON persistence."""

    def __init__(self, bindings: dict[str, list[KeyCombo]] | None = None):
        self._bindings: dict[str, list[KeyCombo]] = bindings or _defaults()

    # ----- query -----------------------------------------------------------

    def action_for_event(self, qt_key: int, qt_modifiers: Qt.KeyboardModifier) -> str | None:
        """Return the action name matching a Qt key event, or None."""
        for action, combos in self._bindings.items():
            for combo in combos:
                if combo.matches_event(qt_key, qt_modifiers):
                    return action
        return None

    def keys_for_action(self, action: str) -> list[KeyCombo]:
        """Return the combos bound to *action* (empty list if unbound)."""
        return list(self._bindings.get(action, []))

    def all_actions(self) -> list[str]:
        """Return all registered action names in sorted order."""
        return sorted(self._bindings.keys())

    # ----- mutation --------------------------------------------------------

    def set_binding(self, action: str, combos: list[KeyCombo]):
        """Replace the combo list for *action*."""
        self._bindings[action] = list(combos)

    def reset_defaults(self):
        """Restore every action to its factory-default binding."""
        self._bindings = _defaults()

    # ----- persistence -----------------------------------------------------

    def save(self, filepath: str):
        """Write current bindings to a JSON file."""
        data = {
            action: [c.to_dict() for c in combos]
            for action, combos in self._bindings.items()
        }
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    @classmethod
    def load(cls, filepath: str) -> "KeyBindings":
        """Load bindings from a JSON file, falling back to defaults.

        If the file is missing or malformed the defaults are used.
        Actions present in the defaults but absent from the file are
        filled in automatically so new features always have a binding.
        """
        defaults = _defaults()
        if not os.path.isfile(filepath):
            return cls(defaults)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return cls(defaults)

        loaded: dict[str, list[KeyCombo]] = {}
        for action, combo_list in raw.items():
            if not isinstance(combo_list, list):
                continue
            combos = []
            for d in combo_list:
                if isinstance(d, dict) and "key" in d:
                    combos.append(KeyCombo.from_dict(d))
            if combos:
                loaded[action] = combos

        # Merge: loaded overrides defaults, but new default actions survive
        merged = dict(defaults)
        merged.update(loaded)
        return cls(merged)
