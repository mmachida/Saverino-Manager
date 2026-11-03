# Saverino Manager

Saverino Manager is a Windows desktop app for organizing game projects, profiles, and verified backups of save files or folders. Backups are stored separately from the original save.

[![Latest release](https://img.shields.io/github/v/release/mmachida/Saverino-Manager?label=version)](https://github.com/mmachida/Saverino-Manager/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-2f5f9e)](https://github.com/mmachida/Saverino-Manager/releases)

<img width="962" height="572" alt="image" src="https://github.com/user-attachments/assets/1efd6f94-2ddc-41c5-8344-fa9e18a8187c" />

## Download and installation

Download the latest Windows x64 package from [GitHub Releases](https://github.com/mmachida/Saverino-Manager/releases).

Each release provides:

- `saverino-manager-windows-x64.zip`, containing Saverino Manager, its updater, and the update manifest.
- `saverino-manager-windows-x64.sha256`, for verifying the ZIP download.

Extract the full ZIP to a writable folder and run `Saverino Manager.exe`. Keep `Saverino Manager Updater.exe` beside it for automatic updates. The packaged app does not require Python.

To verify the download in PowerShell:

```powershell
(Get-FileHash .\saverino-manager-windows-x64.zip -Algorithm SHA256).Hash
Get-Content .\saverino-manager-windows-x64.sha256
```

The hash printed by PowerShell must match the value in the `.sha256` file.

## Features

### Projects and profiles

- Organize saves by project and profile.
- Configure a file or folder as the project's shared save source.
- Choose **App folder**, **Same folder as save**, or **Custom** for backups. New projects use **App folder** by default.
- Create project and profile backup folders as soon as each is added.
- Rename projects and profiles while keeping their managed backup paths in sync.
- Move backup destinations with verified **Copy**, **Move**, or **Cancel** actions and a progress indicator.

App folder stores backups under `Saverino backup` beside the app executable. When running from source, backups are stored in the project directory. App settings, catalog data, logs, and recovery metadata stay in `%APPDATA%\Saverino Manager\`.

### Save management

The save list shows **#**, **Name**, **Description**, and **Created**. Names and descriptions are editable. File saves show their complete filename, including extensions and multiple dots; folder saves show their folder name. File-based projects recognize regular files with any extension, while folder-based projects recognize directories.

The app includes:

- Search, sorting, multi-selection, and drag selection.
- **Create save**, **Overwrite save**, **Load save**, **Delete**, and **Refresh**.
- Automatic selection and scrolling to a newly created save.
- External change detection for added, renamed, modified, and removed backups.
- Project and save context menus, with actions matching the toolbar.
- Buttons to open the original save location and the selected profile's backup folder.

**Refresh** rereads the selected profile's backup folder. It imports valid items added in Explorer and removes missing items from the displayed list, preserving existing descriptions and other save metadata. If the folder cannot be read, the previous list is kept and an error is shown.

**Load save** replaces the configured source only after confirmation. **Overwrite save** replaces the selected backup with the current source and updates its displayed creation time. Project deletion requires typing `delete` and removes managed project backups while preserving the original game save. Profile deletion removes that profile's managed backup folder.

### Auto-Save

Auto-Save schedules can target an entire profile or an individual save. A profile schedule creates new saves; a save schedule overwrites that save. Intervals range from **15 seconds to 7,200 seconds**. The global **Auto-Save ON/OFF** control pauses or resumes all schedules, and active projects and saves are highlighted.

<img width="762" height="422" alt="image" src="https://github.com/user-attachments/assets/3897190d-3e8c-4063-97fd-cebfe9ce1170" />

Auto-Save continues while the window is minimized as long as the app remains open. It is not a Windows service. Saving the Auto-Save settings dialog restarts the countdowns for all enabled schedules, including schedules in other projects.

### Settings, audio, and themes

<img width="702" height="572" alt="image" src="https://github.com/user-attachments/assets/f817a26f-03d6-4d3f-9fab-77d78688e0b1" />

- English (United States) and Portuguese (Brazil).
- Optional startup with Windows; disabled by default.
- Always on top and automatic update checks.
- General Delete shortcut and configurable global create/load hotkeys. Global hotkeys leave keystrokes available to other apps and keep Saverino Manager in the background.
- Independent sound options for importing, loading, and Auto-Save, with shared volume from 0% to 100%.
- Six themes: Classic, Night Mode, Hollow Knight, Silk Song, Elden Ring, and Batman: Arkham Knight. Night Mode is selected on first launch.
- Import and export of project, profile, and application settings. Save files and folders are moved manually and are not included in exports.

## Run from source

Requirements: Windows, Git, and Python 3.12 or newer (below Python 3.15).

Clone the public repository and create a virtual environment:

```powershell
git clone https://github.com/mmachida/Saverino-Manager.git
cd Saverino-Manager
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Start the app with either command:

```powershell
.venv\Scripts\python.exe -m save_manager
```

Application data is stored in `%APPDATA%\Saverino Manager\`. This repository intentionally keeps `.gitignore` out of source control. Before staging changes in a clone, add local data folders and generated `build`, `dist`, and `release` output to `.git/info/exclude` or your global Git ignore file.

## Development checks and release build

Run tests with temporary synthetic files only:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check src tests --select F
```

To create Windows release assets from a Windows development environment, install PyInstaller and run:

```powershell
python -m pip install PyInstaller
python scripts/build_windows.py
```

The script reads the version from `src/save_manager/version.py`, builds both executables, creates the ZIP and SHA-256 file under `dist/`, and includes the update manifest in the ZIP. Upload the ZIP and matching checksum file to the GitHub Release. Keep release binaries out of source commits.

## Updates and data safety

The app checks releases from [mmachida/Saverino-Manager](https://github.com/mmachida/Saverino-Manager). Automatic checks are optional; manual checks are under **About > Check for Updates**. A compatible update is downloaded and verified, then the app asks before closing to install it. The updater replaces program files while preserving project settings and backup folders.

Backup creation and restore run in the background with verification and recovery metadata. The app does not modify the original source when creating a backup. Restoring replaces the configured file or folder with the selected save.

## Limitations

- Windows and local disk paths are the supported environment.
- UNC network paths, symbolic links, and junctions are outside the current scope.
- The app must remain open for Auto-Save timers to run.
- Save payloads are never included in update packages or settings exports.

## Support

Report issues and suggestions through [GitHub Issues](https://github.com/mmachida/Saverino-Manager/issues). To support the project, [Buy me a coffee](https://ko-fi.com/mmachida).
