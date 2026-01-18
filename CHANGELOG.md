# Anvil Level Design Changelog

## 1.0.2

- Fix vertex and edge sliding garbling UVs by disabling 'correct uv' by default (HACK - see etc in Readme for details)
- Improve face local axis calculations to handle 0 length edges

## 1.0.1

- Appropriately scoped keybindings so enabling / disabling the addon doesn't permanently break you keybindings (sorry for any inconvenience caused)
- Keybindings menu in the addon preference (allows you to set all keybindings and for example turn off features by disabling the keybinds)
- Added some general information about exporting to the readme (non addon specific)
- Changed file browser interactions from modal to just a timed function (open models prevent you from reloading scripts)
- Picking material from a different object updates the texture preview in a timely fashion
- Fixed mesh modification reseting scale in some cases
- Fixed loop cuts (actually just force disabled blenders own UV correction which was breaking everything)
- Addon free cam is now blocked in orthographic mode