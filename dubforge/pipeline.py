from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import os
import time
import traceback
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .catalog import LANGUAGES, iso_code, language_label
from .progress import ProjectProgress
from .resources import AdaptiveScheduler, ResourceMonitor
from .store import ProjectStore, atomic_json_write
from .tts_worker import synthesize_task
from .zast_bridge import ZastBridge

EventCallback = Callable[[str, str, float | None], None]


class PipelineInterrupted(RuntimeError):
    """Control-flow signal that must never trigger a computational fallback."""


class DubPipeline:
    def __init__(self, store: ProjectStore, zast_path: str | Path):
        self.store = store
        self.zast_path = Path(zast_path).expanduser().resolve()
        self.progress: ProjectProgress | None = None

    def run(self, project_id: str, callback: EventCallback) -> list[str]:
        project = self.store.get(project_id)
        directory = self.store.project_dir(project_id)
        bridge = ZastBridge(self.zast_path, directory)
        self.progress = ProjectProgress(self.store, project, callback)
        started = time.perf_counter()
        run_status = "failed"
        try:
            source_info = self._prepare_transcription(project, bridge, callback)
            translated = self._translate_all(project, bridge, source_info, callback)
            outputs = self._synthesize_all(project, bridge, source_info, translated, callback)
            project["last_error"] = None
            self.store.save(project)
            self.progress.finish()
            run_status = "completed"
            return outputs
        except Exception as exc:
            project["last_error"] = {
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            self.store.save(project)
            raise
        finally:
            self._record_metric(project, "project_total", time.perf_counter() - started, run_status)
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
                assert self.progress is not None
                self.progress.set("preparation", "Preparação encontrada no cache", 1, 1, force=True)
                if settings["preserve_background"]:
                    self.progress.set("separation", "Separação encontrada no cache", 1, 1, force=True)
                self.progress.set("transcription", "Transcrição encontrada; reutilizando o cache", 1, 1, force=True)
                info["segments"] = transcription["segments"]
                return info

        project["stages"]["import"] = "running"
        project["stages"]["separation"] = "running" if settings["preserve_background"] else "skipped"
        project["stages"]["transcription"] = "running"
        self.store.save(project)

        active_stage: str | None = None
        active_started = 0.0

        def close_stage(status: str = "completed") -> None:
            nonlocal active_stage, active_started
            if active_stage is not None:
                self._record_metric(project, active_stage, time.perf_counter() - active_started, status)
                active_stage = None

        def timed_callback(stage: str, message: str, progress: float | None) -> None:
            nonlocal active_stage, active_started
            if stage in {"import", "separation", "transcription"} and stage != active_stage:
                close_stage()
                active_stage = stage
                active_started = time.perf_counter()
            assert self.progress is not None
            mapped = "preparation" if stage == "import" else stage
            # The reused engine reports coarse global markers rather than
            # stage-local percentages. Keep a truthful 0/100 stage state.
            self.progress.set(mapped, message, 0, 1)

        try:
            info = bridge.prepare_source(
                project["source"]["path"],
                settings["source_language"],
                settings["whisper_model"],
                settings["preserve_background"],
                timed_callback,
            )
        except Exception:
            close_stage("failed")
            raise
        else:
            close_stage()
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
        assert self.progress is not None
        self.progress.set("preparation", "Importação e preparação concluídas", 1, 1, force=True)
        if settings["preserve_background"]:
            self.progress.set("separation", "Separação de voz e ambiente concluída", 1, 1, force=True)
        self.progress.set("transcription", "Transcrição concluída", 1, 1, force=True)
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
        assert self.progress is not None
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

        completed_count = len(completed)
        total_targets = max(1, len(targets))
        self.progress.set(
            "translation",
            f"{completed_count}/{len(targets)} idiomas traduzidos",
            completed_count,
            total_targets,
            force=True,
        )
        try:
            for index, (language, translation_signature) in enumerate(pending):
                short_code = iso_code(LANGUAGES[language])
                lang_state = project["languages"].setdefault(short_code, {"name": language})
                lang_state["translation"] = "running"
                self.store.save(project)
                language_base = completed_count + index

                def language_callback(
                    stage: str,
                    message: str,
                    stage_progress: float | None,
                    *,
                    base: int = language_base,
                    current_language: str = language,
                ) -> None:
                    fraction = 0.0 if stage_progress is None else min(1.0, max(0.0, float(stage_progress)))
                    self.progress.set(
                        "translation",
                        message,
                        base + fraction,
                        total_targets,
                        language=current_language,
                    )

                with self._measure(project, f"translation:{short_code}"):
                    translate_kwargs: dict[str, Any] = {}
                    parameters = inspect.signature(bridge.translate_language).parameters
                    if "generate_normal" in parameters:
                        translate_kwargs.update({
                            "generate_normal": project["settings"].get("generate_srt", True),
                            "batch_size": int(project.get("runtime", {}).get("adaptive", {}).get("translation_batch_size", 8)),
                            "prefer_pytorch": True,
                        })
                    translated = bridge.translate_language(
                        source_info["segments"],
                        source_info.get("detected_language", "pt"),
                        language,
                        project["settings"]["llm_backend"],
                        language_callback,
                        **translate_kwargs,
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
                self.progress.set(
                    "translation",
                    f"Tradução de {language} concluída",
                    language_base + 1,
                    total_targets,
                    language=language,
                    force=True,
                )
        finally:
            if pending:
                # The same backend instance is cached by ZastTranslate's factory.
                # Explicit cleanup releases it before VoxCPM is loaded.
                try:
                    bridge.unload_llm(project["settings"]["llm_backend"])
                except Exception:
                    bridge.release_vram()
        self.progress.complete("translation", "Todas as traduções foram concluídas")
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
        assert self.progress is not None
        voice_reference = settings.get("voice_file") or source_info.get("voice_reference")
        background_path = source_info.get("background") if settings["preserve_background"] else None
        total_segments = sum(len(translated[language]) for language in settings["target_languages"])
        total_segments = max(1, total_segments)
        fractions: dict[str, float] = {}
        plans: list[dict[str, Any]] = []

        for language in settings["target_languages"]:
            short_code = iso_code(LANGUAGES[language])
            lang_state = project["languages"].setdefault(short_code, {"name": language})
            srt_path = directory / "outputs" / short_code / f"{source_stem}_{short_code}.srt"
            if srt_path.exists() and settings.get("generate_srt", True):
                outputs.append(str(srt_path))
            mp3_path = directory / "outputs" / short_code / f"{source_stem}_{short_code}.mp3"
            expected_signature = bridge._synthesis_signature(
                translated[language], voice_reference, settings["never_cut"],
                settings["bitrate"], settings["preserve_background"],
            )
            if mp3_path.exists() and lang_state.get("signature") == expected_signature:
                lang_state["dubbing"] = "completed"
                lang_state["mp3"] = "completed"
                fractions[language] = 1.0
                outputs.append(str(mp3_path))
                continue
            fractions[language] = 0.0
            plans.append({
                "language": language,
                "short_code": short_code,
                "signature": expected_signature,
                "segments": translated[language],
            })

        def update_progress(language: str, message: str, fraction: float) -> None:
            fractions[language] = min(1.0, max(fractions.get(language, 0.0), fraction))
            done_units = sum(
                len(translated[name]) * fractions.get(name, 0.0)
                for name in settings["target_languages"]
            )
            self.progress.set(
                "dubbing", message, done_units, total_segments,
                language=language,
            )

        def accept_result(plan: dict[str, Any], result: dict[str, Any], elapsed: float) -> None:
            language = plan["language"]
            short_code = plan["short_code"]
            lang_state = project["languages"].setdefault(short_code, {"name": language})
            lang_state["dubbing"] = "completed"
            lang_state["mp3"] = "completed"
            lang_state["signature"] = result["signature"]
            lang_state["stats"] = result["stats"]
            self._record_metric(project, f"dubbing:{short_code}", elapsed, "completed")
            outputs.append(result["mp3"])
            update_progress(language, f"Dublagem de {language} concluída", 1.0)
            completed_languages = sum(1 for value in fractions.values() if value >= 1.0)
            self.progress.set(
                "export",
                f"{completed_languages}/{len(settings['target_languages'])} MP3 exportados",
                completed_languages,
                max(1, len(settings["target_languages"])),
                language=language,
                force=True,
            )
            self.store.save(project)

        snapshot = ResourceMonitor(self.store.root).sample()
        scheduler = AdaptiveScheduler(settings.get("adaptive_mode", "Automático"))
        decision = scheduler.decision(snapshot)
        project["runtime"] = {"resource": snapshot.as_dict(), "adaptive": decision}
        self.store.save(project)
        workers = min(len(plans), int(decision["tts_workers"])) if plans else 1
        self.progress.set(
            "dubbing",
            f"Modo {decision['mode']}: {workers} trabalhador(es) VoxCPM",
            sum(len(translated[name]) for name, value in fractions.items() if value >= 1.0),
            total_segments,
            force=True,
        )

        fallback: list[dict[str, Any]] = []
        if workers > 1:
            progress_dir = directory / "cache" / "worker_progress"
            progress_dir.mkdir(parents=True, exist_ok=True)
            tasks: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for plan in plans:
                progress_path = progress_dir / f"{plan['short_code']}.json"
                progress_path.unlink(missing_ok=True)
                task = {
                    "zast_path": str(self.zast_path),
                    "project_dir": str(directory),
                    "translated": plan["segments"],
                    "target_language": plan["language"],
                    "duration": float(source_info["duration"]),
                    "voice_reference": voice_reference,
                    "background_path": background_path,
                    "never_cut": settings["never_cut"],
                    "bitrate": settings["bitrate"],
                    "source_stem": source_stem,
                    "progress_path": str(progress_path),
                    "control_path": str(directory / "cache" / "job_control.json"),
                }
                tasks.append((plan, task))

            context = multiprocessing.get_context("spawn")
            started_by_language = {plan["language"]: time.perf_counter() for plan, _ in tasks}
            try:
                with ProcessPoolExecutor(max_workers=workers, mp_context=context) as executor:
                    future_map = {executor.submit(synthesize_task, task): (plan, task) for plan, task in tasks}
                    pending_futures = set(future_map)
                    while pending_futures:
                        done, pending_futures = wait(pending_futures, timeout=1.0, return_when=FIRST_COMPLETED)
                        for future in done:
                            plan, _ = future_map[future]
                            try:
                                result = future.result()
                            except Exception as exc:
                                plan["parallel_error"] = str(exc)
                                fallback.append(plan)
                            else:
                                elapsed = time.perf_counter() - started_by_language[plan["language"]]
                                accept_result(plan, result, elapsed)
                        for future in pending_futures:
                            plan, task = future_map[future]
                            path = Path(task["progress_path"])
                            try:
                                event = json.loads(path.read_text(encoding="utf-8"))
                                fraction = event.get("progress")
                                update_progress(
                                    plan["language"], event.get("message", "Gerando voz"),
                                    float(fraction) if fraction is not None else fractions[plan["language"]],
                                )
                            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                                update_progress(
                                    plan["language"],
                                    f"Gerando {plan['language']} em {workers} fluxos",
                                    fractions[plan["language"]],
                                )
            except PipelineInterrupted:
                raise
            except Exception:
                finished = {name for name, value in fractions.items() if value >= 1.0}
                fallback.extend(plan for plan in plans if plan["language"] not in finished)
        else:
            fallback = list(plans)

        # A parallel worker may fail because its model did not fit in VRAM. Its
        # segment cache is retained and the language is retried safely with one
        # in-process model, reusing every completed segment.
        unique_fallback = {plan["language"]: plan for plan in fallback if fractions[plan["language"]] < 1.0}
        try:
            for plan in unique_fallback.values():
                language = plan["language"]
                lang_state = project["languages"].setdefault(plan["short_code"], {"name": language})
                lang_state["dubbing"] = "running"
                self.store.save(project)

                def sequential_callback(
                    stage: str,
                    message: str,
                    fraction: float | None,
                    *,
                    current_language: str = language,
                ) -> None:
                    update_progress(current_language, message, float(fraction or 0.0))

                started = time.perf_counter()
                result = bridge.synthesize_language(
                    plan["segments"], language, float(source_info["duration"]),
                    voice_reference, background_path, settings["never_cut"],
                    settings["bitrate"], source_stem, sequential_callback,
                )
                accept_result(plan, result, time.perf_counter() - started)
        finally:
            bridge.unload_tts()

        self.progress.complete("dubbing", "Todas as dublagens foram concluídas")
        self.progress.complete("export", "Todos os arquivos foram exportados")
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

    @contextmanager
    def _measure(self, project: dict[str, Any], operation: str) -> Iterator[None]:
        started = time.perf_counter()
        status = "failed"
        try:
            yield
            status = "completed"
        finally:
            self._record_metric(project, operation, time.perf_counter() - started, status)

    def _record_metric(
        self,
        project: dict[str, Any],
        operation: str,
        seconds: float,
        status: str,
    ) -> None:
        metrics = project.setdefault("metrics", {}).setdefault("operations", {})
        entry = metrics.setdefault(operation, {
            "total_seconds": 0.0,
            "last_seconds": 0.0,
            "runs": 0,
            "status": "pending",
        })
        entry["total_seconds"] = round(float(entry.get("total_seconds", 0.0)) + seconds, 3)
        entry["last_seconds"] = round(seconds, 3)
        entry["runs"] = int(entry.get("runs", 0)) + 1
        entry["status"] = status
        self.store.save(project)


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
    rows.extend(["", metrics_markdown([project])])
    return "\n".join(rows)


def _duration(seconds: float) -> str:
    total = max(0, round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def metrics_markdown(projects: list[dict[str, Any]], title: str = "Métricas e custos") -> str:
    operations: dict[str, float] = {}
    total_processing = 0.0
    source_seconds = 0.0
    output_seconds = 0.0
    settings = projects[0].get("settings", {}) if projects else {}

    labels = {
        "import": "Importação/preparação",
        "separation": "Separação (Demucs)",
        "transcription": "Transcrição (WhisperX)",
    }
    for project in projects:
        project_operations = project.get("metrics", {}).get("operations", {})
        total_processing += float(project_operations.get("project_total", {}).get("total_seconds", 0.0))
        duration = float(project.get("source", {}).get("duration") or 0.0)
        source_seconds += duration
        output_seconds += duration * len(project.get("settings", {}).get("target_languages", []))
        for key, entry in project_operations.items():
            if key == "project_total":
                continue
            operations[key] = operations.get(key, 0.0) + float(entry.get("total_seconds", 0.0))

    lines = [f"### {title}", "| Operação | Tempo acumulado |", "|---|---:|"]
    for key, seconds in operations.items():
        if key.startswith("translation:"):
            code = key.split(":", 1)[1]
            language = next((language_label(p.get("name", code)) for project in projects for c, p in project.get("languages", {}).items() if c == code), code.upper())
            label = f"Tradução — {language}"
        elif key.startswith("dubbing:"):
            code = key.split(":", 1)[1]
            language = next((language_label(p.get("name", code)) for project in projects for c, p in project.get("languages", {}).items() if c == code), code.upper())
            label = f"Dublagem/MP3 — {language}"
        else:
            label = labels.get(key, key)
        lines.append(f"| {label} | {_duration(seconds)} |")
    lines.append(f"| **Processamento total** | **{_duration(total_processing)}** |")

    hourly_usd = float(settings.get("gpu_hourly_usd") or 0.0)
    usd_brl = float(settings.get("usd_brl") or 0.0)
    competitor_rate = float(settings.get("competitor_brl_per_minute") or 2.0)
    vast_brl = total_processing / 3600 * hourly_usd * usd_brl
    panda_brl = output_seconds / 60 * competitor_rate
    lines.extend([
        "",
        f"- Áudio original: **{source_seconds / 60:.1f} min**",
        f"- Minutos dublados (áudio × idiomas): **{output_seconds / 60:.1f} min**",
    ])
    if hourly_usd > 0 and usd_brl > 0:
        lines.extend([
            f"- Custo estimado da Vast: **R$ {vast_brl:.2f}**",
            f"- Custo estimado no Panda: **R$ {panda_brl:.2f}**",
            f"- Economia estimada: **R$ {max(0.0, panda_brl - vast_brl):.2f}**",
        ])
    else:
        lines.append("- Informe o custo da Vast por hora para calcular o valor em reais.")
    return "\n".join(lines)
