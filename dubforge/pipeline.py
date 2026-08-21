from __future__ import annotations

import json
import hashlib
import os
import shutil
import traceback
from pathlib import Path
from typing import Any, Callable, Iterator

from .catalog import LANGUAGES, iso_code, language_label
from .store import ProjectStore, atomic_json_write
from .zast_bridge import ZastBridge


EventCallback = Callable[[str, str, float | None], None]


class DubPipeline:
    def __init__(self, store: ProjectStore, zast_path: str | Path):
        self.store = store
        self.zast_path = Path(zast_path).expanduser().resolve()

    def run(self, project_id: str, callback: EventCallback) -> list[str]:
        project = self.store.get(project_id)
        directory = self.store.project_dir(project_id)
        bridge = ZastBridge(self.zast_path, directory)
        settings = project["settings"]
        try:
            source_info = self._prepare_transcription(project, bridge, callback)
            translated = self._translate_all(project, bridge, source_info, callback)
            outputs = self._synthesize_all(project, bridge, source_info, translated, callback)
            project["last_error"] = None
            self.store.save(project)
            callback("done", "Processamento concluído", 1.0)
            return outputs
        except Exception as exc:
            project["last_error"] = {
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            self.store.save(project)
            raise
        finally:
            bridge.release_vram()

    def _prepare_transcription(
        self,
        project: dict[str, Any],
        bridge: ZastBridge,
        callback: EventCallback,
    ) -> dict[str, Any]:
        directory = self.store.project_dir(project["id"])
        info_path = directory / "cache" / "source_info.json"
        transcription_path = directory / "cache" / "transcription.json"
        settings = project["settings"]
        transcription_signature = self._signature({
            "source": self._file_identity(Path(project["source"]["path"])),
            "source_language": settings["source_language"],
            "whisper_model": settings["whisper_model"],
            "preserve_background": settings["preserve_background"],
        })

        if info_path.exists() and transcription_path.exists():
            info = json.loads(info_path.read_text(encoding="utf-8"))
            transcription = json.loads(transcription_path.read_text(encoding="utf-8"))
            if transcription.get("signature") == transcription_signature:
                callback("transcription", "Transcrição encontrada; reutilizando o cache", 0.25)
                info["segments"] = transcription["segments"]
                return info

        project["stages"]["import"] = "running"
        project["stages"]["separation"] = "running" if settings["preserve_background"] else "skipped"
        project["stages"]["transcription"] = "running"
        self.store.save(project)

        info = bridge.prepare_source(
            project["source"]["path"],
            settings["source_language"],
            settings["whisper_model"],
            settings["preserve_background"],
            callback,
        )
        segments = info.pop("segments")
        atomic_json_write(transcription_path, {
            "language": info.get("detected_language"),
            "signature": transcription_signature,
            "segments": segments,
        })
        atomic_json_write(info_path, info)
        info["segments"] = segments

        project["source"]["duration"] = info.get("duration")
        project["source"]["detected_language"] = info.get("detected_language")
        project["stages"]["import"] = "completed"
        project["stages"]["separation"] = "completed" if settings["preserve_background"] else "skipped"
        project["stages"]["transcription"] = "completed"
        self.store.save(project)
        return info

    def _translate_all(
        self,
        project: dict[str, Any],
        bridge: ZastBridge,
        source_info: dict[str, Any],
        callback: EventCallback,
    ) -> dict[str, list[dict[str, Any]]]:
        directory = self.store.project_dir(project["id"])
        targets = project["settings"]["target_languages"]
        completed: dict[str, list[dict[str, Any]]] = {}
        pending = []
        transcription_signature = self._signature({
            "language": source_info.get("detected_language"),
            "segments": [
                [segment.get("start"), segment.get("end"), segment.get("text")]
                for segment in source_info["segments"]
            ],
        })
        for language in targets:
            short_code = iso_code(LANGUAGES[language])
            path = directory / "translations" / f"{short_code}.json"
            lang_state = project["languages"].setdefault(short_code, {"name": language})
            expected_signature = self._signature({
                "transcription": transcription_signature,
                "language": language,
                "language_code": LANGUAGES[language],
                "llm_backend": project["settings"]["llm_backend"],
            })
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if saved and saved.get("signature") == expected_signature and lang_state.get("translation") == "completed":
                completed[language] = saved["segments"]
                self._write_srt(project, language, completed[language])
                lang_state["srt"] = "completed" if project["settings"].get("generate_srt", True) else "skipped"
                callback(short_code, f"Tradução de {language} já concluída", None)
            else:
                pending.append((language, expected_signature))

        try:
            for index, (language, translation_signature) in enumerate(pending):
                short_code = iso_code(LANGUAGES[language])
                lang_state = project["languages"].setdefault(short_code, {"name": language})
                lang_state["translation"] = "running"
                self.store.save(project)
                translated = bridge.translate_language(
                    source_info["segments"],
                    source_info.get("detected_language", "pt"),
                    language,
                    project["settings"]["llm_backend"],
                    callback,
                )
                path = directory / "translations" / f"{short_code}.json"
                atomic_json_write(path, {
                    "language": language,
                    "language_code": LANGUAGES[language],
                    "signature": translation_signature,
                    "segments": translated,
                })
                completed[language] = translated
                lang_state["translation"] = "completed"
                lang_state["fitting"] = "completed"
                self._write_srt(project, language, translated)
                lang_state["srt"] = "completed" if project["settings"].get("generate_srt", True) else "skipped"
                lang_state["translation_signature"] = translation_signature
                self.store.save(project)
        finally:
            if pending:
                # The same backend instance is cached by ZastTranslate's factory.
                # Explicit cleanup releases it before VoxCPM is loaded.
                try:
                    bridge.unload_llm(project["settings"]["llm_backend"])
                except Exception:
                    bridge.release_vram()
        return completed

    def _write_srt(
        self,
        project: dict[str, Any],
        language: str,
        segments: list[dict[str, Any]],
    ) -> str | None:
        if not project["settings"].get("generate_srt", True):
            return None
        directory = self.store.project_dir(project["id"])
        short_code = iso_code(LANGUAGES[language])
        output_dir = directory / "outputs" / short_code
        output_dir.mkdir(parents=True, exist_ok=True)
        source_stem = Path(project["source"]["original_name"]).stem
        srt_path = output_dir / f"{source_stem}_{short_code}.srt"
        lines: list[str] = []
        for index, segment in enumerate(segments, 1):
            lines.extend([
                str(index),
                f"{self._timestamp(segment['start'])} --> {self._timestamp(segment['end'])}",
                segment.get("normal_text") or segment.get("translated_text", ""),
                "",
            ])
        temp_path = output_dir / f".{srt_path.name}.tmp"
        temp_path.write_text("\n".join(lines), encoding="utf-8-sig")
        os.replace(temp_path, srt_path)
        return str(srt_path)

    def _synthesize_all(
        self,
        project: dict[str, Any],
        bridge: ZastBridge,
        source_info: dict[str, Any],
        translated: dict[str, list[dict[str, Any]]],
        callback: EventCallback,
    ) -> list[str]:
        directory = self.store.project_dir(project["id"])
        settings = project["settings"]
        outputs: list[str] = []
        source_stem = Path(project["source"]["original_name"]).stem
        try:
            for language in settings["target_languages"]:
                short_code = iso_code(LANGUAGES[language])
                lang_state = project["languages"].setdefault(short_code, {"name": language})
                srt_path = directory / "outputs" / short_code / f"{source_stem}_{short_code}.srt"
                if srt_path.exists() and settings.get("generate_srt", True):
                    outputs.append(str(srt_path))

                mp3_path = directory / "outputs" / short_code / f"{source_stem}_{short_code}.mp3"
                expected_signature = bridge._synthesis_signature(
                    translated[language],
                    settings.get("voice_file") or source_info.get("voice_reference"),
                    settings["never_cut"],
                    settings["bitrate"],
                    settings["preserve_background"],
                )
                if mp3_path.exists() and lang_state.get("signature") == expected_signature:
                    lang_state["dubbing"] = "completed"
                    lang_state["mp3"] = "completed"
                    outputs.append(str(mp3_path))
                    callback(short_code, f"MP3 de {language} já concluído", None)
                    continue

                lang_state["dubbing"] = "running"
                self.store.save(project)
                result = bridge.synthesize_language(
                    translated[language],
                    language,
                    float(source_info["duration"]),
                    settings.get("voice_file") or source_info.get("voice_reference"),
                    source_info.get("background") if settings["preserve_background"] else None,
                    settings["never_cut"],
                    settings["bitrate"],
                    source_stem,
                    callback,
                )
                lang_state["dubbing"] = "completed"
                lang_state["mp3"] = "completed"
                lang_state["signature"] = result["signature"]
                lang_state["stats"] = result["stats"]
                self.store.save(project)
                outputs.append(result["mp3"])
        finally:
            bridge.unload_tts()
        return sorted(set(outputs))

    @staticmethod
    def _timestamp(seconds: float) -> str:
        milliseconds = max(0, round(float(seconds) * 1000))
        hours, milliseconds = divmod(milliseconds, 3_600_000)
        minutes, milliseconds = divmod(milliseconds, 60_000)
        secs, milliseconds = divmod(milliseconds, 1_000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

    @staticmethod
    def _signature(payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _file_identity(path: Path) -> list[Any]:
        stat = path.stat()
        return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


def status_markdown(project: dict[str, Any] | None, message: str = "") -> str:
    if not project:
        return "Nenhum projeto aberto."
    icons = {
        "completed": "✅", "running": "🔵", "pending": "⚪",
        "failed": "🔴", "skipped": "➖",
    }
    rows = [
        "| Etapa | Estado |",
        "|---|:---:|",
        f"| Importação | {icons.get(project['stages'].get('import'), '⚪')} |",
        f"| Separação | {icons.get(project['stages'].get('separation'), '⚪')} |",
        f"| Transcrição | {icons.get(project['stages'].get('transcription'), '⚪')} |",
    ]
    for code, state in project.get("languages", {}).items():
        name = language_label(state.get("name", code.upper()))
        rows.append(f"| {name}: tradução | {icons.get(state.get('translation'), '⚪')} |")
        rows.append(f"| {name}: dublagem/MP3 | {icons.get(state.get('mp3') or state.get('dubbing'), '⚪')} |")
    if message:
        rows.extend(["", f"**Agora:** {message}"])
    if project.get("last_error"):
        rows.extend(["", f"**Último erro:** `{project['last_error'].get('message', '')}`"])
    return "\n".join(rows)
