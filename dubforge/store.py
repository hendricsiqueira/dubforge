from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "projeto"


def atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for manifest in self.root.glob("*/project.json"):
            try:
                data = self._read_json(manifest)
                projects.append(data)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(projects, key=lambda item: item.get("updated_at", ""), reverse=True)

    def get(self, project_id: str) -> dict[str, Any]:
        manifest = self.project_dir(project_id) / "project.json"
        if not manifest.exists():
            raise FileNotFoundError(f"Projeto não encontrado: {project_id}")
        return self._read_json(manifest)

    def create(self, name: str, source_path: str | Path, settings: dict[str, Any]) -> dict[str, Any]:
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Arquivo de origem não encontrado: {source}")

        base_id = slugify(name or source.stem)
        project_id = base_id
        suffix = 2
        while (self.root / project_id).exists():
            project_id = f"{base_id}-{suffix}"
            suffix += 1

        directory = self.project_dir(project_id)
        for child in ["source", "cache/work", "translations", "audio_segments", "outputs"]:
            (directory / child).mkdir(parents=True, exist_ok=True)

        stored_source = directory / "source" / source.name
        shutil.copy2(source, stored_source)
        now = utc_now()
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "id": project_id,
            "name": name.strip() or source.stem,
            "created_at": now,
            "updated_at": now,
            "source": {
                "original_name": source.name,
                "path": str(stored_source),
                "duration": None,
                "detected_language": None,
            },
            "settings": copy.deepcopy(settings),
            "stages": {
                "import": "pending",
                "separation": "pending",
                "transcription": "pending",
            },
            "languages": {},
            "last_error": None,
        }
        self.save(data)
        return data

    def save(self, project: dict[str, Any]) -> None:
        project["updated_at"] = utc_now()
        atomic_json_write(self.project_dir(project["id"]) / "project.json", project)

    def update_settings(self, project: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        project["settings"] = copy.deepcopy(settings)
        self.save(project)
        return project

    def persist_voice_file(
        self,
        project: dict[str, Any],
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        source_value = settings.get("voice_file")
        if not source_value:
            settings["voice_file"] = None
            return settings
        source = Path(source_value).resolve()
        project_dir = self.project_dir(project["id"])
        existing_dir = (project_dir / "cache").resolve()
        if existing_dir == source.parent or existing_dir in source.parents:
            settings["voice_file"] = str(source)
            return settings
        if not source.is_file():
            raise FileNotFoundError(f"Referência de voz não encontrada: {source}")
        suffix = source.suffix.lower() or ".wav"
        destination = project_dir / "cache" / f"user_voice_reference{suffix}"
        shutil.copy2(source, destination)
        settings["voice_file"] = str(destination)
        return settings

    def project_dir(self, project_id: str) -> Path:
        safe_id = slugify(project_id)
        candidate = (self.root / safe_id).resolve()
        if self.root not in candidate.parents:
            raise ValueError("Identificador de projeto inválido")
        return candidate

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


class BatchStore:
    """Persistent queue metadata. Individual jobs remain normal projects."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list_batches(self) -> list[dict[str, Any]]:
        batches: list[dict[str, Any]] = []
        for manifest in self.root.glob("*.json"):
            try:
                batches.append(ProjectStore._read_json(manifest))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(batches, key=lambda item: item.get("updated_at", ""), reverse=True)

    def get(self, batch_id: str) -> dict[str, Any]:
        manifest = self._path(batch_id)
        if not manifest.exists():
            raise FileNotFoundError(f"Lote não encontrado: {batch_id}")
        return ProjectStore._read_json(manifest)

    def create(self, name: str, project_ids: list[str]) -> dict[str, Any]:
        now = utc_now()
        base_id = slugify(name or "lote")
        batch_id = base_id
        suffix = 2
        while self._path(batch_id).exists():
            batch_id = f"{base_id}-{suffix}"
            suffix += 1
        data = {
            "schema_version": 1,
            "id": batch_id,
            "name": name.strip() or "Lote de dublagem",
            "created_at": now,
            "updated_at": now,
            "status": "pending",
            "current_project_id": None,
            "project_ids": project_ids,
            "last_error": None,
        }
        self.save(data)
        return data

    def save(self, batch: dict[str, Any]) -> None:
        batch["updated_at"] = utc_now()
        atomic_json_write(self._path(batch["id"]), batch)

    def _path(self, batch_id: str) -> Path:
        return self.root / f"{slugify(batch_id)}.json"
