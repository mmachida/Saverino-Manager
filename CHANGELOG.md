# Changelog

## v1.1.0 — Changes since v1.0.3

### New

- Add **Refresh** to reconcile the selected profile's save list with files and folders on disk.
- Add Auto-Save schedules for profiles and individual saves, with a global pause control, active indicators, optional sound, and 15–7,200 second intervals.
- Add **App folder** as the default backup destination for new projects.
- Add project rename support and use **Project/Projects** terminology throughout the interface.

### Modified

- Show progress for backup destination changes and offer **Copy**, **Move**, or **Cancel**. A verified Move removes empty folders left behind at the old location.
- Save Auto-Save settings by restarting the countdown for enabled schedules.
- Improve Classic theme contrast for the selected project, Auto-Save saves, and Auto-Save ON/OFF labels.
- Expand the README with current features and instructions for cloning and running the source project.

### Fixed

- Rename the existing managed project backup folder and update its metadata instead of leaving an extra old folder.
- Resolve App folder from the project directory when running `run.bat`, while keeping application data in `%APPDATA%`.
- Check Windows process liveness without signaling the process that owns the instance lock.
- Keep project and profile backup paths stable during destination changes and remove only verified old folders.

## v1.0.3 — Changes since v1.0.2

### New

- Create the game backup folder when a game is added.
- Create the profile folder when a profile is added.

### Modified

- Keep the entire save row selected while preserving the Name cell as the active editing cell after creating or loading a save.
- Automatically select the previous save, or the next one when needed, after deleting a save.
- Follow the new save when scrolling, including when the list is sorted by creation date.
- Use a moderate green style for the Load Save button across all themes, including the matching button relief in Classic and Night Mode.
- Remove the complete managed Saverino - GameName folder when deleting a game while preserving the original save.

### Fixed

- Allow F2 to rename a save immediately after creating or loading it.
- Keep global hotkeys from blocking the same key in other applications or bringing Saverino Manager to the foreground.
- Prevent empty game backup folders from remaining after game deletion.
## v1.0.2 — Save filename and layout fixes

### New

- Show and edit the complete save filename, including extensions and multiple dots.

### Fix

- Recognize every file in file-based profiles, including names containing dots.
- Give the save list more room for the Name column.


## v1.0.0 — Initial Release

The first public release of Saverino Manager for Windows.

- Manage games, profiles, and file or folder save backups.
- Create, overwrite, load, rename, and delete saves safely.
- Search, filter, edit descriptions, and reorder games by drag and drop.
- Detect manually added or renamed backup files and folders.
- Choose from six color themes, including Night Mode and game-inspired themes.
- English and Portuguese interface translations.
- Local and global hotkeys, sound effects, and configurable preferences.
- Import and export application settings and game/profile configuration.
- Background integrity checks, recovery support, and GitHub release updates.

## v1.0.1 — Extensionless file snapshots

A maintenance release focused on consistent file-save naming and loading.

- Store file snapshots without a physical extension in the backup folder.
- Restore file snapshots to the original source path, including its original extension.
- Apply the same naming rule when creating, renaming, or importing saves manually.
- Keep compatibility with legacy snapshots that still use the source file extension.



