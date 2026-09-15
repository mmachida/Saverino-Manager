# <img width="24" height="24" alt="logo256" src="https://github.com/user-attachments/assets/206514d9-c936-4582-9ae9-8f76639fde3f" /> Saverino Manager

Saverino Manager is a local save manager for Windows. It organizes games, profiles, and backup copies of files or folders while keeping backups separate from the original save.

[![Latest release](https://img.shields.io/github/v/release/mmachida/Saverino-Manager?label=version)](https://github.com/mmachida/Saverino-Manager/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-2f5f9e)](https://github.com/mmachida/Saverino-Manager/releases)

## Screenshots

<img width="962" height="572" alt="image" src="https://github.com/user-attachments/assets/f552bc58-6e5f-427a-a00b-cf9fd43e3cf7" />
<img width="649" height="569" alt="image" src="https://github.com/user-attachments/assets/b0fd9309-6e6d-4745-9a55-30daa3b41f1a" />


## Download and installation

Download the latest version from the [Releases](https://github.com/mmachida/Saverino-Manager/releases) page.

The current Windows x64 package contains:

- `save-manager-windows-x64.zip`: application package.
- `save-manager-windows-x64.sha256`: checksum for verifying the download.

Extract the entire ZIP to a writable folder and run `SaveManager.exe`. Keep `SaveManagerUpdater.exe` in the same folder because it is used by the update system. The distributed application does not require Python or any additional installation.

You can optionally verify the ZIP in PowerShell:

```powershell
(Get-FileHash .\save-manager-windows-x64.zip -Algorithm SHA256).Hash
Get-Content .\save-manager-windows-x64.sha256
```

The hexadecimal value printed by the first command must match the value in the `.sha256` file.

## First configuration

1. Open **File > Add Game**.
2. Enter the game name, with a maximum of 50 characters and only characters valid for Windows folder names.
3. Choose whether the source is a **File** or a **Folder**, then select the original save.
4. Choose the backup destination: **Same folder as save** or **Custom**.
5. Create a profile with **New profile**.
6. Use **Create save** to make the first backup.

The source and destination are configured once per game and apply to all profiles of that game. Backups are organized as follows:

```text
Saverino - GameName\ProfileName\...
```

Each profile has its own folder. The original save remains at the location selected by the user.

## Save management

The list displays `#`, `Name`, `Description`, and `Created`. Names and descriptions can be edited with a double-click; names follow Windows filename rules. Automatically created saves use names such as `save_01`, `save_02`, and so on.

The application provides:

- search and sorting;
- multiple selection with Ctrl, Shift, Alt, or mouse dragging;
- context menus for games and saves;
- **Create save**, **Overwrite save**, **Load save**, and **Delete**;
- buttons to open the source folder and backup folder;
- automatic selection of a newly created save and scrolling to the end of the list.

**Load save** replaces the current source contents only after confirmation. It does not create an additional automatic backup; the user chooses which state to load. **Overwrite save** replaces the selected backup with the current source state.

Files and folders added manually to a profile directory are synchronized with the list. When the source is a file, only files with the same extension as the source are considered. Changes made directly in Explorer, including renames, are detected by the application.

Deleting a save asks for confirmation and removes it from the active list. Deleting a profile removes its backup folder and all saves managed inside it. Deleting a game removes its profiles and managed backups but keeps the original game file or folder.

## Safety and synchronization

Copy and restore operations run in the background with content verification and recovery for interrupted operations. The application does not change the source when creating a backup. During a restore, the source is replaced with the selected save and the result is verified.

The catalog follows the real state of the folders: if a profile backup folder is removed manually, the profile disappears after synchronization. If a backup is missing, it is marked unavailable until it returns to the expected location.

## Settings

**Options > Settings** includes:

- English (United States) and Portuguese (Brazil);
- initialize with Windows, disabled by default;
- always on top;
- check for updates on startup;
- global hotkeys for creating and loading saves;
- **Load save without confirmation** for the load hotkey;
- individual buttons to clear each hotkey;
- independent sound effects for importing and loading saves;
- sound volume from 0% to 100%, with a default of 50%.

The general `Delete` shortcut works while the application window is focused and triggers deletion of the selected save. Global hotkeys work while the application is in the background and include a delay to prevent duplicate operations.

## Themes

The cards in **Options > Themes** are ordered as follows:

1. Classic
2. Night Mode
3. Hollow Knight
4. Silk Song
5. Elden Ring
6. Batman: Arkham Knight

**Night Mode** is used on the first launch. The selected theme is applied immediately and saved for future launches. Every theme keeps the same selection, hover, button, disabled-state, scrollbar, and dialog behavior while changing only the color palette.

## Import and export settings

**File > Import/Export** can export or import:

- game settings;
- profiles;
- application preferences;
- language, theme, audio, and hotkey settings.

Save files and folders **are not included** in the exported file. After a clean installation, move backups manually to the paths registered in game settings before importing or reconnecting the data.

## Data storage

Application settings are stored in:

```text
%APPDATA%\Saverino Manager\
```

The catalog, recovery journals, metadata, internal trash, and logs are stored inside this directory. Backups remain in the destinations selected by the user and are not copied to `%APPDATA%`.

## Updates

The application checks public releases from [mmachida/Saverino-Manager](https://github.com/mmachida/Saverino-Manager). Checks can run automatically at startup or manually through **About > Check for Updates**.

When a compatible update is found, the application downloads the package, validates its checksum, and asks for confirmation before closing to install it. The updater replaces only program files; games, profiles, settings, and backups remain preserved.

Each published release must contain the ZIP and matching `.sha256` file for its target architecture. For Windows x64, the expected names are `save-manager-windows-x64.zip` and `save-manager-windows-x64.sha256`.

## Known limitations

- Current support is for Windows only.
- The application must remain in a folder with read and write permission for updates and logs.

## Support

Report problems and suggestions through [GitHub Issues](https://github.com/mmachida/Saverino-Manager/issues). To support the project, [Buy me a coffee](https://ko-fi.com/mmachida).
