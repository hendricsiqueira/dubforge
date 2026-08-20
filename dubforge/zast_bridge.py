from __future__ import annotations

import gc
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .catalog import LANGUAGES, SOURCE_LANGUAGE_CODES, iso_code


ProgressCallback = Callable[[str, str, float | None], None]


class ZastBridge:
    """Use the user's existing ZastTranslate installation as an engine library."""

    def __init__(self, zast_path: str | Path, project_dir: str | Path):
        self.zast_path = Path(zast_path).expanduser().resolve()
        self.project_dir = Path(project_dir).resolve()
        self.work_dir = self.project_dir / "cache" / "work"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.venv_scripts = Path(sys.executable).resolve().parent
        self._prepend_to_path(self.venv_scripts)
        self._validate_installation()
        self._bootstrap_modules()

    @staticmethod
    def _prepend_to_path(directory: str | Path) -> None:
        """Expose console scripts from the Python environment to child processes."""
        directory_text = str(Path(directory).resolve())
        current_entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
        normalized = {os.path.normcase(os.path.abspath(entry)) for entry in current_entries}
        if os.path.normcase(os.path.abspath(directory_text)) not in normalized:
            os.environ["PATH"] = os.pathsep.join([directory_text, *current_entries])

    def _require_command(self, command: str, purpose: str) -> str:
        executable = shutil.which(command)
        if executable:
            return executable

        suffix = ".exe" if os.name == "nt" else ""
        expected = self.venv_scripts / f"{command}{suffix}"
        raise FileNotFoundError(
            f"O executável '{command}' necessário para {purpose} não foi encontrado. "
            f"O DubForge já adicionou o ambiente do ZastTranslate ao PATH, mas esperava "
            f"encontrá-lo em '{expected}'. Feche o DubForge e confirme se esse arquivo existe."
        )

    def _validate_installation(self) -> None:
        required = [
            self.zast_path / "config.py",
            self.zast_path / "modules" / "transcriber.py",
            self.zast_path / "modules" / "reformulator.py",
            self.zast_path / "modules" / "tts_backends" / "factory.py",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "ZastTranslate não foi encontrado ou está incompleto em "
                f"{self.zast_path}. Arquivos ausentes: {', '.join(missing)}"
            )

    def _bootstrap_modules(self) -> None:
        path_text = str(self.zast_path)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)

        existing = sys.modules.get("config")
        if existing is not None:
            existing_file = Path(getattr(existing, "__file__", "")).resolve()
            if existing_file != (self.zast_path / "config.py").resolve():
                raise RuntimeError(
                    "Outro módulo chamado 'config' foi carregado antes do ZastTranslate. "
                    "Feche e abra o DubForge novamente."
                )

        self.zconfig = importlib.import_module("config")
        self.zconfig.TEMP_DIR = str(self.work_dir)
        self.zconfig.OUTPUT_DIR = str(self.project_dir / "outputs")

        self.downloader_module = importlib.import_module("modules.downloader")
        self.separator_module = importlib.import_module("modules.separator")
        self.transcriber_module = importlib.import_module("modules.transcriber")
        self.reformulator_module = importlib.import_module("modules.reformulator")
        self.time_sync_module = importlib.import_module("modules.time_sync")
        self.audio_mixer_module = importlib.import_module("modules.audio_mixer")
        self.srt_module = importlib.import_module("modules.srt_parser")
        self.tts_factory = importlib.import_module("modules.tts_backends.factory")
        self.cps_module = importlib.import_module("fitted_cps_config")

        self._set_temp_dir(self.work_dir)

    def _set_temp_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.zconfig.TEMP_DIR = str(path)
        self.downloader_module.TEMP_DIR = str(path)
        self.separator_module.TEMP_DIR = str(path)
        self.time_sync_module.TEMP_DIR = str(path)

    def prepare_source(
        self,
        source_path: str | Path,
        source_language: str,
        whisper_model: str,
        preserve_background: bool,
        callback: ProgressCallback,
    ) -> dict[str, Any]:
        self._set_temp_dir(self.work_dir)
        callback("import", "Extraindo o áudio do arquivo original", 0.05)
        downloader = self.downloader_module.VideoDownloader()
        info = downloader.import_local(str(source_path))
        ext = Path(source_path).suffix.lower()
        info["is_audio_only"] = ext in {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}

        if preserve_background:
            callback("separation", "Separando voz e áudio ambiente com Demucs", 0.12)
            self._require_command("demucs", "separar voz e áudio ambiente")
            separator = self.separator_module.VocalSeparator()
            stems = separator.separate(info["audio_44k"])
            info["vocals"] = stems["vocals"]
            info["background"] = stems["background"]
        else:
            info["vocals"] = info["audio_44k"]
            info["background"] = None

        callback("transcription", f"Transcrevendo com WhisperX {whisper_model}", 0.20)
        transcriber = self.transcriber_module.Transcriber(model_size=whisper_model)
        result = transcriber.transcribe(
            info["audio_16k"],
            language=SOURCE_LANGUAGE_CODES.get(source_language),
            enable_diarization=False,
        )
        transcriber.cleanup()
        info["detected_language"] = result.get("language") or SOURCE_LANGUAGE_CODES.get(source_language) or "pt"
        info["segments"] = result["segments"]

        self._extract_voice_reference(info)
        self.release_vram()
        return info

    def _extract_voice_reference(self, info: dict[str, Any]) -> None:
        import soundfile as sf

        segments = info.get("segments", [])
        if not segments:
            raise RuntimeError("A transcrição terminou sem segmentos de fala.")
        candidates = [s for s in segments if 5 <= (s["end"] - s["start"]) <= 15]
        best = max(candidates or segments, key=lambda s: s["end"] - s["start"])
        data, sample_rate = sf.read(info["vocals"])
        start = max(0, int(best["start"] * sample_rate))
        end = min(len(data), int(best["end"] * sample_rate))
        reference_path = self.project_dir / "cache" / "voice_reference.wav"
        sf.write(reference_path, data[start:end], sample_rate)
        text_path = self.project_dir / "cache" / "voice_reference.txt"
        text_path.write_text(best.get("text", "").strip(), encoding="utf-8")
        info["voice_reference"] = str(reference_path)

    def translate_language(
        self,
        segments: list[dict[str, Any]],
        source_language_code: str,
        target_language: str,
        llm_backend: str,
        callback: ProgressCallback,
    ) -> list[dict[str, Any]]:
        import copy

        target_code = LANGUAGES[target_language]
        short_code = iso_code(target_code)
        cps = self.cps_module.get_fitted_cps(short_code)
        reformulator = self.reformulator_module.Reformulator(backend_name=llm_backend)
        translated = copy.deepcopy(segments)
        callback(short_code, f"Traduzindo e ajustando {target_language}", None)
        translated = reformulator.translate_segments(
            translated,
            source_language_code,
            target_language,
            target_code,
            cps=cps,
            speed_factor=1.0,
        )

        for segment in translated:
            fitted = segment.get("translated_text", "")
            duration = segment["end"] - segment["start"]
            max_chars = int(duration * cps)
            if fitted.strip() and len(fitted) > max_chars * 1.1:
                shorter = reformulator.shorten(fitted, max_chars, target_code)
                if shorter and len(shorter) < len(fitted):
                    segment["translated_text"] = shorter
                    segment["reformulated"] = True

        callback(short_code, f"Gerando legenda natural em {target_language}", None)
        reformulator.translate_normal(translated, source_language_code, target_code)
        return translated

    def unload_llm(self, llm_backend: str) -> None:
        factory = importlib.import_module("modules.llm_backends.factory")
        active = getattr(factory, "_active_llm_backend", None)
        if active is not None:
            active.unload()
            factory._active_llm_backend = None
        self.release_vram()

    def synthesize_language(
        self,
        translated: list[dict[str, Any]],
        target_language: str,
        duration: float,
        voice_reference: str | None,
        background_path: str | None,
        never_cut: bool,
        bitrate: str,
        source_stem: str,
        callback: ProgressCallback,
    ) -> dict[str, Any]:
        target_code = LANGUAGES[target_language]
        short_code = iso_code(target_code)
        signature = self._synthesis_signature(
            translated, voice_reference, never_cut, bitrate, bool(background_path)
        )
        segment_dir = self.project_dir / "audio_segments" / short_code / signature[:12]
        self._set_temp_dir(segment_dir)

        tts = self.tts_factory.get_backend("VoxCPM 2")
        callback(short_code, f"Carregando VoxCPM 2 para {target_language}", None)
        tts.load(ref_audio_path=voice_reference)

        # Deliberately do not attach the LLM here. This guarantees that Qwen is
        # not reloaded while VoxCPM occupies VRAM on 12 GB cards.
        time_sync = self.time_sync_module.TimeSync(tts, reformulator=None)
        callback(short_code, f"Gerando a voz em {target_language}", None)
        if never_cut:
            synced, stats = time_sync.sync_all_never_cut(
                translated, short_code, duration, voice_mapping=None, gender="Woman"
            )
            voice_track = time_sync.build_full_audio(
                synced, duration, use_real_positions=True
            )
        else:
            translated, _ = time_sync.pre_check_and_shorten(translated, target_code)
            synced, stats = time_sync.sync_all(
                translated,
                short_code,
                voice_mapping=None,
                total_duration=duration,
                gender="Woman",
            )
            voice_track = time_sync.build_full_audio(synced, duration)

        output_dir = self.project_dir / "outputs" / short_code
        output_dir.mkdir(parents=True, exist_ok=True)
        mixed_wav = segment_dir / "mixed.wav"
        if background_path:
            callback(short_code, "Misturando voz e áudio ambiente", None)
            mixer = self.audio_mixer_module.AudioMixer()
            mixer.mix(voice_track, background_path, str(mixed_wav))
        else:
            shutil.copy2(voice_track, mixed_wav)

        mp3_path = output_dir / f"{source_stem}_{short_code}.mp3"
        temp_mp3 = output_dir / f".{mp3_path.name}.tmp.mp3"
        callback(short_code, f"Exportando MP3 {bitrate}", None)
        ffmpeg = self._require_command("ffmpeg", "exportar o MP3")
        subprocess.run(
            [ffmpeg, "-y", "-i", str(mixed_wav), "-vn", "-codec:a", "libmp3lame", "-b:a", bitrate, str(temp_mp3)],
            check=True,
            capture_output=True,
        )
        os.replace(temp_mp3, mp3_path)
        return {
            "mp3": str(mp3_path),
            "stats": stats,
            "signature": signature,
            "segment_cache": str(segment_dir),
        }

    def unload_tts(self) -> None:
        try:
            backend = self.tts_factory.get_backend("VoxCPM 2")
            backend.cleanup()
        finally:
            self.release_vram()

    @staticmethod
    def _synthesis_signature(
        translated: list[dict[str, Any]],
        voice_reference: str | None,
        never_cut: bool,
        bitrate: str,
        preserve_background: bool,
    ) -> str:
        voice_stat = None
        if voice_reference and Path(voice_reference).exists():
            stat = Path(voice_reference).stat()
            voice_stat = [stat.st_size, stat.st_mtime_ns]
        payload = {
            "segments": [
                [s.get("start"), s.get("end"), s.get("translated_text")]
                for s in translated
            ],
            "voice": voice_stat,
            "never_cut": never_cut,
            "bitrate": bitrate,
            "background": preserve_background,
            "tts": "VoxCPM 2",
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def release_vram() -> None:
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass
