from __future__ import annotations

import json
import os
import shutil
import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from ..domain.models import Game, GameSettings, Profile, Snapshot, SnapshotKind, SnapshotStatus, SourceType, StorageMode

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS games (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT
);
CREATE TABLE IF NOT EXISTS game_settings (
 game_id TEXT PRIMARY KEY REFERENCES games(id),
 source_type TEXT NOT NULL, source_path TEXT NOT NULL,
 storage_mode TEXT NOT NULL, backup_base_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
 id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), name TEXT NOT NULL,
 normalized_name TEXT NOT NULL, source_type TEXT NOT NULL, source_path TEXT NOT NULL,
 storage_mode TEXT NOT NULL, backup_base_path TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT,
 UNIQUE(game_id, normalized_name)
);
CREATE TABLE IF NOT EXISTS snapshots (
 id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES profiles(id), name TEXT NOT NULL,
 normalized_name TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL,
 total_bytes INTEGER NOT NULL DEFAULT 0, file_count INTEGER NOT NULL DEFAULT 0,
 relative_path TEXT NOT NULL, status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', last_loaded_at TEXT,
 UNIQUE(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS snapshots_profile_name_active ON snapshots(profile_id, normalized_name) WHERE status <> 'trashed';
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations (
 id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, snapshot_id TEXT, kind TEXT NOT NULL,
 phase TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, error_code TEXT
);
"""


class CatalogDB:
    # Initialize the object.
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"Catálogo inválido preservado em {self.path}; use a recuperação para reconstruí-lo") from exc

    # Connect.
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    # Handle connection.
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    # Initialize.
    def _initialize(self) -> None:
        with self.connection() as conn:
            version_table = conn.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone()
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0] if version_table else 0
            if version > 2:
                raise RuntimeError("Versão do catálogo mais recente que este aplicativo")
            if version == 1:
                backup_path = self.path.with_suffix(self.path.suffix + ".before-game-settings.bak")
                if not backup_path.exists():
                    backup = sqlite3.connect(backup_path)
                    try:
                        conn.backup(backup)
                    finally:
                        backup.close()
            conn.executescript(SCHEMA)
            if version < 2:
                # Só migrar automaticamente quando os perfis concordam. Em caso
                # de conflito, preservar todos os caminhos até configuração explícita.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for row in conn.execute("SELECT id FROM games").fetchall():
                        choices = conn.execute(
                            "SELECT DISTINCT source_type,source_path,storage_mode,backup_base_path FROM profiles WHERE game_id=?",
                            (row["id"],),
                        ).fetchall()
                        if len(choices) == 1:
                            conn.execute("INSERT OR IGNORE INTO game_settings VALUES (?,?,?,?,?)", (row["id"], *choices[0]))
                    conn.execute("UPDATE schema_version SET version=2")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

    @contextmanager
    # Handle transaction.
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    # Handle game.
    def _game(row: sqlite3.Row) -> Game:
        return Game(row["name"], UUID(row["id"]), row["created_at"], row["updated_at"], row["archived_at"])

    # Add game.
    def add_game(self, game: Game, settings: GameSettings | None = None) -> None:
        with self.transaction() as c:
            c.execute("INSERT INTO games VALUES (?,?,?,?,?,?)", (str(game.id), game.name, game.normalized_name, game.created_at, game.updated_at, game.archived_at))
            if settings:
                c.execute("INSERT INTO game_settings VALUES (?,?,?,?,?)", (str(game.id), settings.source_type.value, settings.source_path, settings.storage_mode.value, settings.backup_base_path))

    # Update game.
    def update_game(self, game: Game) -> None:
        with self.transaction() as c:
            c.execute("UPDATE games SET name=?,normalized_name=?,updated_at=?,archived_at=? WHERE id=?", (game.name, game.normalized_name, game.updated_at, game.archived_at, str(game.id)))

    # Delete game.
    def delete_game(self, game_id: UUID) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM snapshots WHERE profile_id IN (SELECT id FROM profiles WHERE game_id=?)", (str(game_id),))
            c.execute("DELETE FROM profiles WHERE game_id=?", (str(game_id),))
            c.execute("DELETE FROM game_settings WHERE game_id=?", (str(game_id),))
            c.execute("DELETE FROM games WHERE id=?", (str(game_id),))

    # Get game.
    def get_game(self, game_id: UUID) -> Game | None:
        with self.connection() as c:
            row = c.execute("SELECT * FROM games WHERE id=?", (str(game_id),)).fetchone()
        return Game(row["name"], UUID(row["id"]), row["created_at"], row["updated_at"], row["archived_at"]) if row else None

    # List games.
    def list_games(self, include_archived: bool = False) -> list[Game]:
        query = "SELECT * FROM games" + ("" if include_archived else " WHERE archived_at IS NULL") + " ORDER BY normalized_name"
        with self.connection() as c:
            rows = c.execute(query).fetchall()
        return [Game(r["name"], UUID(r["id"]), r["created_at"], r["updated_at"], r["archived_at"]) for r in rows]

    # Get game settings.
    def get_game_settings(self, game_id: UUID) -> GameSettings | None:
        with self.connection() as c:
            row = c.execute("SELECT * FROM game_settings WHERE game_id=?", (str(game_id),)).fetchone()
        return GameSettings(row["source_type"], row["source_path"], row["storage_mode"], row["backup_base_path"]) if row else None

    # Set game settings.
    def set_game_settings(self, game_id: UUID, settings: GameSettings, operation_id: str | None = None) -> None:
        values = (settings.source_type.value, settings.source_path, settings.storage_mode.value, settings.backup_base_path)
        with self.transaction() as c:
            c.execute(
                "INSERT INTO game_settings VALUES (?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET "
                "source_type=excluded.source_type,source_path=excluded.source_path,"
                "storage_mode=excluded.storage_mode,backup_base_path=excluded.backup_base_path",
                (str(game_id), *values),
            )
            # As colunas antigas são preservadas para recuperação de catálogos legados.
            c.execute("UPDATE profiles SET source_type=?,source_path=?,storage_mode=?,backup_base_path=? WHERE game_id=?", (*values, str(game_id)))
            if operation_id:
                c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (f"game-settings-commit:{game_id}", json.dumps(operation_id)))

    # Add profile.
    def add_profile(self, profile: Profile) -> None:
        self._apply_game_settings(profile)
        with self.transaction() as c:
            c.execute("INSERT INTO profiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (str(profile.id), str(profile.game_id), profile.name, profile.normalized_name, profile.source_type.value, profile.source_path, profile.storage_mode.value, profile.backup_base_path, profile.notes, profile.created_at, profile.updated_at, profile.archived_at))

    # Update profile.
    def update_profile(self, profile: Profile) -> None:
        self._apply_game_settings(profile)
        with self.transaction() as c:
            c.execute("UPDATE profiles SET name=?,normalized_name=?,source_type=?,source_path=?,storage_mode=?,backup_base_path=?,notes=?,updated_at=?,archived_at=? WHERE id=?", (profile.name, profile.normalized_name, profile.source_type.value, profile.source_path, profile.storage_mode.value, profile.backup_base_path, profile.notes, profile.updated_at, profile.archived_at, str(profile.id)))

    # Delete profile.
    def delete_profile(self, profile_id: UUID) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM snapshots WHERE profile_id=?", (str(profile_id),))
            c.execute("DELETE FROM profiles WHERE id=?", (str(profile_id),))

    # Get profile.
    def get_profile(self, profile_id: UUID) -> Profile | None:
        with self.connection() as c:
            r = c.execute("SELECT * FROM profiles WHERE id=?", (str(profile_id),)).fetchone()
        return self._profile(r) if r else None

    # Apply game settings.
    def _apply_game_settings(self, profile: Profile) -> Profile:
        settings = self.get_game_settings(profile.game_id)
        if settings:
            profile.source_type, profile.source_path = settings.source_type, settings.source_path
            profile.storage_mode, profile.backup_base_path = settings.storage_mode, settings.backup_base_path
        return profile

    # Handle profile.
    def _profile(self, r: sqlite3.Row) -> Profile:
        return self._apply_game_settings(Profile(UUID(r["game_id"]), r["name"], SourceType(r["source_type"]), r["source_path"], StorageMode(r["storage_mode"]), r["backup_base_path"], r["notes"], UUID(r["id"]), r["created_at"], r["updated_at"], r["archived_at"]))

    # List profiles.
    def list_profiles(self, game_id: UUID, include_archived: bool = False) -> list[Profile]:
        q = "SELECT * FROM profiles WHERE game_id=?" + ("" if include_archived else " AND archived_at IS NULL") + " ORDER BY normalized_name"
        with self.connection() as c:
            rows = c.execute(q, (str(game_id),)).fetchall()
        return [self._profile(r) for r in rows]

    # Add snapshot.
    def add_snapshot(self, snap: Snapshot) -> None:
        with self.transaction() as c:
            c.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (str(snap.id), str(snap.profile_id), snap.name, snap.normalized_name, snap.kind.value, snap.created_at, snap.total_bytes, snap.file_count, snap.relative_path, snap.status.value, snap.notes, snap.last_loaded_at))

    # Update snapshot.
    def update_snapshot(self, snap: Snapshot) -> None:
        with self.transaction() as c:
            c.execute("UPDATE snapshots SET name=?,normalized_name=?,kind=?,created_at=?,total_bytes=?,file_count=?,relative_path=?,status=?,notes=?,last_loaded_at=? WHERE id=?", (snap.name, snap.normalized_name, snap.kind.value, snap.created_at, snap.total_bytes, snap.file_count, snap.relative_path, snap.status.value, snap.notes, snap.last_loaded_at, str(snap.id)))

    # Delete snapshot.
    def delete_snapshot(self, snapshot_id: UUID) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM snapshots WHERE id=?", (str(snapshot_id),))

    @staticmethod
    # Handle snapshot.
    def _snapshot(r: sqlite3.Row) -> Snapshot:
        return Snapshot(UUID(r["profile_id"]), r["name"], SnapshotKind(r["kind"]), r["notes"], r["total_bytes"], r["file_count"], r["relative_path"], SnapshotStatus(r["status"]), UUID(r["id"]), r["created_at"], r["last_loaded_at"])

    # Get snapshot.
    def get_snapshot(self, snapshot_id: UUID) -> Snapshot | None:
        with self.connection() as c:
            r = c.execute("SELECT * FROM snapshots WHERE id=?", (str(snapshot_id),)).fetchone()
        return self._snapshot(r) if r else None

    # List snapshots.
    def list_snapshots(self, profile_id: UUID, include_trashed: bool = False) -> list[Snapshot]:
        q = "SELECT * FROM snapshots WHERE profile_id=?" + ("" if include_trashed else " AND status <> 'trashed'") + " ORDER BY created_at DESC,id DESC"
        with self.connection() as c:
            rows = c.execute(q, (str(profile_id),)).fetchall()
        return [self._snapshot(r) for r in rows]

    # Set setting.
    def set_setting(self, key: str, value: object) -> None:
        with self.transaction() as c:
            c.execute("INSERT INTO settings(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, json.dumps(value, ensure_ascii=False)))

    # Get setting.
    def get_setting(self, key: str, default=None):
        with self.connection() as c:
            r = c.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    # Export catalog.
    def export_to(self, destination: Path) -> Path:
        """Write settings and metadata without backup payloads."""
        destination = Path(destination).expanduser()
        if destination.resolve() == self.path.resolve():
            raise ValueError("O arquivo de exportação precisa ser diferente do catálogo atual")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        catalog_copy = destination.with_name(f".{destination.name}.{uuid4().hex}.sqlite3")
        try:
            source = self.connect()
            target = sqlite3.connect(catalog_copy)
            try:
                source.backup(target)
                # Exports contain configuration only; payload indexes are rebuilt from moved files.
                target.execute("DELETE FROM snapshots")
                target.execute("DELETE FROM operations")
                target.commit()
            finally:
                target.close()
                source.close()
            metadata_root = self.path.parent / "profiles"
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "export.json",
                    json.dumps({"format": "saverino-manager", "version": 1}, ensure_ascii=False),
                )
                archive.write(catalog_copy, "catalog.sqlite3")
                if metadata_root.is_dir():
                    for metadata in metadata_root.rglob("*.json"):
                        if metadata.is_file() and metadata.name == "profile.json":
                            archive.write(metadata, f"profiles/{metadata.relative_to(metadata_root).as_posix()}")
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            try:
                catalog_copy.unlink()
            except FileNotFoundError:
                pass
        return destination

    # Validate catalog.
    @staticmethod
    def _validate_catalog_file(path: Path) -> None:
        """Validate schema and integrity before importing."""
        required = {"schema_version", "games", "game_settings", "profiles", "snapshots", "settings", "operations"}
        connection = sqlite3.connect(path)
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not required.issubset(tables):
                raise ValueError("O arquivo não é uma exportação válida do Saverino Manager")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ValueError("O arquivo de exportação está corrompido")
            version = connection.execute("SELECT version FROM schema_version").fetchone()
            if not version or int(version[0]) > 2:
                raise ValueError("A versão do arquivo de exportação não é compatível")
        finally:
            connection.close()

    # Extract catalog export.
    @staticmethod
    def _extract_export(source: Path, temporary: Path, metadata_root: Path) -> bool:
        """Extract a catalog archive and its profile metadata."""
        if not zipfile.is_zipfile(source):
            shutil.copy2(source, temporary)
            return False
        with zipfile.ZipFile(source) as archive:
            try:
                descriptor = json.loads(archive.read("export.json").decode("utf-8"))
                catalog_info = archive.getinfo("catalog.sqlite3")
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("O arquivo de exportação está incompleto") from exc
            if descriptor != {"format": "saverino-manager", "version": 1}:
                raise ValueError("O formato da exportação não é compatível")
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("O arquivo de exportação contém um caminho inválido")
                if name.startswith("profiles/") and not name.endswith("/"):
                    if path.suffix.casefold() != ".json" or len(path.parts) < 3:
                        raise ValueError("O arquivo de exportação contém metadados inválidos")
                    try:
                        value = json.loads(archive.read(info).decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError("O arquivo de exportação contém metadados inválidos") from exc
                    if not isinstance(value, dict):
                        raise ValueError("O arquivo de exportação contém metadados inválidos")
                    target = metadata_root.joinpath(*path.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            with archive.open(catalog_info) as origin, temporary.open("wb") as target:
                shutil.copyfileobj(origin, target)
        return True

    # Import catalog.
    def import_from(self, source: Path) -> Path:
        """Replace the catalog with a validated SQLite export."""
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError("O arquivo de configurações não existe")
        if source == self.path.resolve():
            raise ValueError("Selecione um arquivo de configurações diferente do catálogo atual")
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.import")
        metadata_temporary = self.path.parent / f".profiles.{uuid4().hex}.import"
        metadata_temporary.mkdir(parents=True, exist_ok=True)
        archive_import = False
        try:
            try:
                archive_import = self._extract_export(source, temporary, metadata_temporary)
            except zipfile.BadZipFile as exc:
                raise ValueError("O arquivo de exportação está corrompido") from exc
            self._validate_catalog_file(temporary)
            os.replace(temporary, self.path)
            if archive_import:
                current_metadata = self.path.parent / "profiles"
                old_metadata = self.path.parent / f".profiles.{uuid4().hex}.previous"
                if current_metadata.exists():
                    current_metadata.rename(old_metadata)
                metadata_temporary.rename(current_metadata)
                shutil.rmtree(old_metadata, ignore_errors=True)
                metadata_temporary = None
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            if metadata_temporary is not None:
                shutil.rmtree(metadata_temporary, ignore_errors=True)
        return self.path
