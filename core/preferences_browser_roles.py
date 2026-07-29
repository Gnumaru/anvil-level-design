"""Persistent roles for Anvil browsers hosted in Preferences areas."""

import json

import bpy

from .logging import debug_log


BROWSER_ROLE_TEXTURE = 'TEXTURE'
BROWSER_ROLE_PREFAB = 'PREFAB'
BROWSER_ROLES = frozenset({
    BROWSER_ROLE_TEXTURE,
    BROWSER_ROLE_PREFAB,
})

_SCREEN_ROLES_KEY = "anvil_preferences_browser_roles"
_SCREEN_ROLES_VERSION = 1
_runtime_roles = {}
_restored_screens = set()


def area_is_preferences(area):
    if area is None:
        return False
    try:
        return (
            area.type == 'PREFERENCES'
            or getattr(area, "ui_type", "") == 'PREFERENCES'
        )
    except ReferenceError:
        return False


def _screen_areas(screen):
    if screen is None:
        return []
    try:
        return list(screen.areas)
    except ReferenceError:
        return []


def _screen_bounds(screen):
    areas = _screen_areas(screen)
    if not areas:
        return 0.0, 0.0, 1.0, 1.0
    try:
        min_x = min(area.x for area in areas)
        min_y = min(area.y for area in areas)
        max_x = max(area.x + area.width for area in areas)
        max_y = max(area.y + area.height for area in areas)
    except ReferenceError:
        return 0.0, 0.0, 1.0, 1.0
    return (
        float(min_x),
        float(min_y),
        float(max(1, max_x - min_x)),
        float(max(1, max_y - min_y)),
    )


def _area_slot(screen, area):
    min_x, min_y, screen_width, screen_height = _screen_bounds(screen)
    try:
        return {
            "x": round((area.x + area.width * 0.5 - min_x) / screen_width, 6),
            "y": round((area.y + area.height * 0.5 - min_y) / screen_height, 6),
            "w": round(area.width / screen_width, 6),
            "h": round(area.height / screen_height, 6),
        }
    except ReferenceError:
        return None


def _slot_distance(saved_slot, live_slot):
    return (
        abs(float(saved_slot.get("x", 0.0)) - live_slot["x"])
        + abs(float(saved_slot.get("y", 0.0)) - live_slot["y"])
        + abs(float(saved_slot.get("w", 0.0)) - live_slot["w"]) * 0.5
        + abs(float(saved_slot.get("h", 0.0)) - live_slot["h"]) * 0.5
    )


def _read_entries(screen):
    try:
        encoded = screen.get(_SCREEN_ROLES_KEY, "")
    except ReferenceError:
        return []
    if not encoded:
        return []
    try:
        data = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        debug_log(f"[BrowserRoles] Could not decode saved roles: {exc}")
        return []
    if not isinstance(data, dict) or data.get("version") != _SCREEN_ROLES_VERSION:
        return []
    entries = data.get("areas", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _runtime_entries_for_screen(screen):
    live_areas = set(_screen_areas(screen))
    entries = []
    for area, role in list(_runtime_roles.items()):
        try:
            if area not in live_areas or not area_is_preferences(area):
                continue
        except ReferenceError:
            continue
        slot = _area_slot(screen, area)
        if slot is None:
            continue
        slot["role"] = role
        entries.append(slot)
    entries.sort(key=lambda entry: entry["role"])
    return entries


def _write_screen(screen):
    entries = _runtime_entries_for_screen(screen)
    if entries:
        encoded = json.dumps(
            {
                "version": _SCREEN_ROLES_VERSION,
                "areas": entries,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            if screen.get(_SCREEN_ROLES_KEY, "") != encoded:
                screen[_SCREEN_ROLES_KEY] = encoded
        except (ReferenceError, TypeError):
            pass
        return
    try:
        if screen.get(_SCREEN_ROLES_KEY) is not None:
            del screen[_SCREEN_ROLES_KEY]
    except (KeyError, ReferenceError, TypeError):
        pass


def _restore_screen(screen):
    if screen is None:
        return
    try:
        screen_pointer = screen.as_pointer()
    except ReferenceError:
        return
    if screen_pointer in _restored_screens:
        return
    _restored_screens.add(screen_pointer)

    available_areas = [
        area for area in _screen_areas(screen)
        if area_is_preferences(area)
    ]
    used_areas = set()
    restored_roles = set()
    for entry in _read_entries(screen):
        role = entry.get("role")
        if role not in BROWSER_ROLES or role in restored_roles:
            continue
        candidates = [area for area in available_areas if area not in used_areas]
        candidate_slots = [
            (area, _area_slot(screen, area))
            for area in candidates
        ]
        candidate_slots = [
            (area, slot) for area, slot in candidate_slots
            if slot is not None
        ]
        if not candidate_slots:
            continue
        area, _slot = min(
            candidate_slots,
            key=lambda candidate: _slot_distance(entry, candidate[1]),
        )
        _runtime_roles[area] = role
        used_areas.add(area)
        restored_roles.add(role)


def browser_role_for_area(screen, area):
    _restore_screen(screen)
    try:
        return _runtime_roles.get(area)
    except ReferenceError:
        return None


def browser_area_for_role(screen, role):
    _restore_screen(screen)
    live_areas = set(_screen_areas(screen))
    for area, assigned_role in list(_runtime_roles.items()):
        try:
            if area in live_areas and assigned_role == role:
                return area
        except ReferenceError:
            continue
    return None


def assign_browser_role(screen, area, role):
    if role not in BROWSER_ROLES:
        raise ValueError(f"Unknown browser role: {role}")
    if area not in _screen_areas(screen) or not area_is_preferences(area):
        raise ValueError("The browser host is no longer a Preferences area")
    _restore_screen(screen)
    live_areas = set(_screen_areas(screen))
    for assigned_area, assigned_role in list(_runtime_roles.items()):
        try:
            if assigned_area in live_areas and (
                    assigned_area == area or assigned_role == role):
                del _runtime_roles[assigned_area]
        except ReferenceError:
            del _runtime_roles[assigned_area]
    _runtime_roles[area] = role
    _write_screen(screen)


def clear_browser_role(screen, area):
    _restore_screen(screen)
    try:
        if area in _runtime_roles:
            del _runtime_roles[area]
    except ReferenceError:
        pass
    _write_screen(screen)


def sync_browser_roles():
    live_areas = set()
    for screen in bpy.data.screens:
        live_areas.update(_screen_areas(screen))
    for area in list(_runtime_roles.keys()):
        try:
            if area not in live_areas or not area_is_preferences(area):
                del _runtime_roles[area]
        except ReferenceError:
            del _runtime_roles[area]
    for screen in bpy.data.screens:
        _restore_screen(screen)
        _write_screen(screen)


def reset_browser_roles():
    _runtime_roles.clear()
    _restored_screens.clear()
