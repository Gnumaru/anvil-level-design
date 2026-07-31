"""Version-specific persistence with automatic texture browser migration."""

import json
import os

import bpy

from ..core.logging import debug_log


_TEXTURE_BROWSER_DATA_VERSION = 2
_TEXTURE_BROWSER_DATA_DIRECTORY = "anvil_level_design"
_TEXTURE_BROWSER_DATA_FILENAME = "texture_browser.json"
_V2_SCALAR_PROPERTIES = {
    "preview_scale": "texture_browser_preview_scale",
    "filters_initialized": "texture_browser_filters_initialized",
    "last_folder_path": "texture_browser_last_folder_path",
    "active_include_index": "texture_browser_active_include_index",
    "active_exclude_index": "texture_browser_active_exclude_index",
}
_loading_texture_browser_data = False
_texture_browser_data_save_blocked = False
_texture_browser_saves_suspended = False


def texture_browser_data_filepath():
    config_directory = bpy.utils.user_resource('CONFIG')
    if not config_directory:
        return ""
    return os.path.join(
        config_directory,
        _TEXTURE_BROWSER_DATA_DIRECTORY,
        _TEXTURE_BROWSER_DATA_FILENAME,
    )


def _older_data():
    config_directory = os.path.normpath(bpy.utils.user_resource('CONFIG'))
    if os.path.basename(config_directory).lower() != "config":
        return None

    version_directory = os.path.dirname(config_directory)
    current_version = tuple(bpy.app.version[:2])
    candidates = []
    try:
        for entry in os.scandir(os.path.dirname(version_directory)):
            parts = entry.name.split(".")
            if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            version = int(parts[0]), int(parts[1])
            filepath = os.path.join(
                entry.path,
                "config",
                _TEXTURE_BROWSER_DATA_DIRECTORY,
                _TEXTURE_BROWSER_DATA_FILENAME,
            )
            if entry.is_dir() and version < current_version and os.path.isfile(filepath):
                candidates.append((version, filepath))
    except OSError as exc:
        debug_log(f"[TextureBrowser] Could not inspect older settings: {exc}")

    for version, filepath in sorted(candidates, reverse=True):
        try:
            data = _read_data(filepath)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            debug_log(f"[TextureBrowser] Skipping invalid data in {filepath}: {exc}")
            continue
        if _has_user_content(data):
            return version, filepath, data
    return None


def texture_browser_saves_suspended():
    return _texture_browser_saves_suspended


def set_texture_browser_saves_suspended(suspended):
    global _texture_browser_saves_suspended
    _texture_browser_saves_suspended = suspended


def _addon_preferences():
    package_name = __package__.split(".", 1)[0]
    addon = bpy.context.preferences.addons.get(package_name)
    return addon.preferences if addon is not None else None


def _texture_browser_properties_available(preferences):
    return (
        preferences is not None
        and hasattr(preferences, "texture_browser_favorites")
        and hasattr(preferences, "texture_browser_collections")
    )


def _texture_browser_data(preferences):
    data = {
        "version": _TEXTURE_BROWSER_DATA_VERSION,
        "favorites": [
            {"name": favorite.name, "path": favorite.path}
            for favorite in preferences.texture_browser_favorites
        ],
        "active_favorite_index": preferences.texture_browser_active_favorite_index,
        "collections": [
            {
                "name": collection.name,
                "files": [item.filepath for item in collection.files],
                "active_file_index": collection.active_file_index,
            }
            for collection in preferences.texture_browser_collections
        ],
        "active_collection_index": preferences.texture_browser_active_collection_index,
        "include_suffixes": [
            item.suffix for item in preferences.texture_browser_include_suffixes
        ],
        "exclude_suffixes": [
            item.suffix for item in preferences.texture_browser_exclude_suffixes
        ],
    }
    for data_name, property_name in _V2_SCALAR_PROPERTIES.items():
        data[data_name] = getattr(preferences, property_name)
    return data


def save_texture_browser_data():
    if (
            _loading_texture_browser_data
            or _texture_browser_data_save_blocked
            or _texture_browser_saves_suspended):
        return False

    preferences = _addon_preferences()
    if not _texture_browser_properties_available(preferences):
        return False

    filepath = texture_browser_data_filepath()
    if not filepath:
        print(
            "Anvil Level Design: Error saving texture browser data: "
            "Blender user config directory is unavailable",
            flush=True,
        )
        return False

    temporary_filepath = filepath + ".tmp"
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(temporary_filepath, "w", encoding="utf-8") as file:
            json.dump(_texture_browser_data(preferences), file, indent=2)
            file.write("\n")
        os.replace(temporary_filepath, filepath)
    except (OSError, TypeError, ValueError) as exc:
        print(
            f"Anvil Level Design: Error saving texture browser data: {exc}",
            flush=True,
        )
        return False

    debug_log(f"[TextureBrowser] Saved user data to {filepath}")
    return True


def _read_data(filepath):
    with open(filepath, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("root value must be an object")
    if data.get("version") not in {1, _TEXTURE_BROWSER_DATA_VERSION}:
        raise ValueError(f"unsupported version {data.get('version')!r}")
    return data


def _has_user_content(data):
    return data["version"] == _TEXTURE_BROWSER_DATA_VERSION or bool(
        data.get("favorites") or data.get("collections")
    )


def _populate_texture_browser_data(preferences, data):
    favorites = data.get("favorites", [])
    collections = data.get("collections", [])
    if not isinstance(favorites, list) or not isinstance(collections, list):
        raise ValueError("favorites and collections must be lists")

    if data["version"] == _TEXTURE_BROWSER_DATA_VERSION:
        for data_name, property_name in _V2_SCALAR_PROPERTIES.items():
            if data_name in data:
                setattr(preferences, property_name, data[data_name])
        for data_name, property_name in (
                ("include_suffixes", "texture_browser_include_suffixes"),
                ("exclude_suffixes", "texture_browser_exclude_suffixes")):
            suffixes = data.get(data_name, [])
            if not isinstance(suffixes, list):
                raise ValueError("suffixes must be lists")
            collection = getattr(preferences, property_name)
            collection.clear()
            for suffix in suffixes:
                if isinstance(suffix, str):
                    collection.add().suffix = suffix

    preferences.texture_browser_favorites.clear()
    for favorite_data in favorites:
        if isinstance(favorite_data, dict):
            favorite = preferences.texture_browser_favorites.add()
            favorite.name = favorite_data.get("name", "")
            favorite.path = favorite_data.get("path", "")
    preferences.texture_browser_active_favorite_index = data.get(
        "active_favorite_index", 0
    )

    preferences.texture_browser_collections.clear()
    for collection_data in collections:
        if not isinstance(collection_data, dict):
            continue
        collection = preferences.texture_browser_collections.add()
        collection.name = collection_data.get("name", "")
        for filepath in collection_data.get("files", []):
            if isinstance(filepath, str):
                collection.files.add().filepath = filepath
        collection.active_file_index = collection_data.get("active_file_index", 0)
    preferences.texture_browser_active_collection_index = data.get(
        "active_collection_index", 0
    )


def load_texture_browser_data():
    global _loading_texture_browser_data, _texture_browser_data_save_blocked

    preferences = _addon_preferences()
    if not _texture_browser_properties_available(preferences):
        return False

    filepath = texture_browser_data_filepath()
    if not filepath:
        return False

    migrated_from = None
    try:
        data = _read_data(filepath) if os.path.isfile(filepath) else None
        if data is None or not _has_user_content(data):
            old_data = _older_data()
            if old_data is not None and (data is None or _has_user_content(old_data[2])):
                version, source_filepath, data = old_data
                migrated_from = version, source_filepath
        if data is None:
            _texture_browser_data_save_blocked = False
            return save_texture_browser_data()

        _loading_texture_browser_data = True
        _populate_texture_browser_data(preferences, data)
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
        _texture_browser_data_save_blocked = True
        print(
            f"Anvil Level Design: Error loading texture browser data: {exc}",
            flush=True,
        )
        return False
    finally:
        _loading_texture_browser_data = False

    _texture_browser_data_save_blocked = False
    if migrated_from is not None:
        version, source_filepath = migrated_from
        if not save_texture_browser_data():
            return False
        print(
            "Anvil Level Design: Migrated texture browser settings "
            f"from Blender {version[0]}.{version[1]}",
            flush=True,
        )
        debug_log(f"[TextureBrowser] Migrated {source_filepath} to {filepath}")
        return True
    if data["version"] == 1:
        return save_texture_browser_data()

    debug_log(f"[TextureBrowser] Loaded user data from {filepath}")
    return True
