"""Context action support for repeating the selected prefab."""

from dataclasses import dataclass

from .assets import (
    find_prefab_asset_reference_owner,
    object_has_linked_prefab_dependency,
    resolve_prefab_asset_reference,
    resolve_prefab_from_object,
    set_prefab_asset_reference,
)


@dataclass(frozen=True)
class RepeatPrefabAction:
    resolved_prefab: dict
    resolution_error: str
    should_report_error: bool


_modal_override = None


def set_modal_override(asset_reference, active_object):
    global _modal_override
    _modal_override = (asset_reference, active_object)


def clear_modal_override():
    global _modal_override
    _modal_override = None


def get_modal_override_reference(active_object):
    if _modal_override is None:
        return ""
    asset_reference, owner = _modal_override
    try:
        if active_object == owner:
            return asset_reference
    except ReferenceError:
        pass
    clear_modal_override()
    return ""


def _modal_override_matches(active_object):
    if _modal_override is None:
        return False
    _asset_reference, owner = _modal_override
    try:
        return active_object == owner
    except ReferenceError:
        return False


def validate_modal_override(active_object):
    """Clear a modal repeat source after the user changes active object."""
    if _modal_override is None or _modal_override_matches(active_object):
        return False
    clear_modal_override()
    return True


def has_repeat_prefab_candidate(active_object):
    """Cheap, read-only availability check for panel drawing and polling."""
    if _modal_override_matches(active_object):
        return True
    if find_prefab_asset_reference_owner(active_object) is not None:
        return True
    return object_has_linked_prefab_dependency(active_object)


def set_repeat_source(obj, asset_reference):
    if obj is not None:
        set_prefab_asset_reference(obj, asset_reference)
    clear_modal_override()


def resolve_repeat_prefab(scene, active_object):
    override_reference = get_modal_override_reference(active_object)
    if override_reference:
        resolved, error = resolve_prefab_asset_reference(scene, override_reference)
        if resolved is None:
            return RepeatPrefabAction(None, error, True)
        resolved["repeat_object"] = None
        return RepeatPrefabAction(resolved, "", False)

    has_reference = find_prefab_asset_reference_owner(active_object) is not None
    resolved, error = resolve_prefab_from_object(scene, active_object)
    if resolved is not None:
        return RepeatPrefabAction(resolved, "", False)
    should_report = has_reference or error == "Selected object matches multiple prefab assets"
    if should_report:
        return RepeatPrefabAction(None, error, True)
    return None
