from __future__ import annotations

import os
import threading
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Iterator

import gradio as gr

from .catalog import (
    DEFAULT_TARGETS,
    LANGUAGE_CHOICES,
    MP3_BITRATES,
    SOURCE_LANGUAGE_CHOICES,
    WHISPER_MODELS,
)
from .pipeline import DubPipeline, status_markdown
from .store import BatchStore, ProjectStore


APP_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = Path(os.environ.get("DUBFORGE_PROJECTS_DIR", APP_ROOT / "projects")).resolve()
ZAST_PATH = Path(os.environ.get("ZAST_TRANSLATE_PATH", APP_ROOT.parent / "ZastTranslate")).resolve()
STORE = ProjectStore(PROJECTS_ROOT)
BATCHES = BatchStore(PROJECTS_ROOT / "batches")
PIPELINE_LOCK = threading.Lock()


def project_choices() -> list[tuple[str, str]]:
    choices = [("➕ Novo projeto", "__new__")]
    for item in STORE.list_projects():
        choices.append((f"{item['name']} · {item['updated_at'][:10]}", item["id"]))
    return choices


def batch_choices() -> list[tuple[str, str]]:
    labels = [("Selecione um lote para retomar", "")]
    for batch in BATCHES.list_batches():
        completed = sum(
            STORE.get(project_id).get("stages", {}).get("transcription") == "completed"
            for project_id in batch["project_ids"]
            if (STORE.project_dir(project_id) / "project.json").exists()
        )
        labels.append((
            f"{batch['name']} · {completed}/{len(batch['project_ids'])} processados · {batch['status']}",
            batch["id"],
        ))
    return labels


def _source_paths(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


def _settings(
    source_language: str, whisper_model: str, target_languages: list[str], voice_mode: str,
    voice_file: str | None, sync_mode: str, bitrate: str, generate_srt: bool,
    preserve_background: bool,
) -> dict[str, Any]:
    return {
        "source_language": source_language,
        "whisper_model": whisper_model,
        "target_languages": target_languages,
        "voice_mode": voice_mode,
        "voice_file": voice_file if voice_mode == "Arquivo de referência" else None,
        "never_cut": sync_mode == "Nunca cortar a fala",
        "bitrate": bitrate,
        "generate_srt": generate_srt,
        "preserve_background": preserve_background,
        "llm_backend": "Qwen2.5-7B-Instruct",
        "tts_backend": "VoxCPM 2",
    }


def load_project(project_id: str):
    if not project_id or project_id == "__new__":
        return (
            "", None, "Portuguese", "large-v3", DEFAULT_TARGETS,
            "Clonar voz original", None, "Priorizar sincronismo", "320k",
            True, True, status_markdown(None), [],
        )
    project = STORE.get(project_id)
    settings = project["settings"]
    output_files = [str(path) for path in STORE.project_dir(project_id).glob("outputs/**/*") if path.is_file()]
    return (
        project["name"], None, settings["source_language"], settings["whisper_model"],
        settings["target_languages"], settings["voice_mode"], settings.get("voice_file"),
        "Nunca cortar a fala" if settings["never_cut"] else "Priorizar sincronismo",
        settings["bitrate"], settings["generate_srt"], settings["preserve_background"],
        status_markdown(project), output_files,
    )


def refresh_projects():
    return gr.update(choices=project_choices())


def refresh_batches():
    return gr.update(choices=batch_choices())


def run_project(
    selected_project: str,
    project_name: str,
    source_file: str | list[str] | None,
    source_language: str,
    whisper_model: str,
    target_languages: list[str],
    voice_mode: str,
    voice_file: str | None,
    sync_mode: str,
    bitrate: str,
    generate_srt: bool,
    preserve_background: bool,
) -> Iterator[tuple[str, list[str], Any, str]]:
    if not target_languages:
        raise gr.Error("Selecione pelo menos um idioma de destino.")
    source_paths = _source_paths(source_file)
    if selected_project == "__new__" and not source_paths:
        raise gr.Error("Escolha um vídeo ou áudio para criar o projeto.")
    if voice_mode == "Arquivo de referência" and not voice_file:
        raise gr.Error("Escolha um arquivo de referência de voz.")
    if not ZAST_PATH.exists():
        raise gr.Error(f"ZastTranslate não encontrado em: {ZAST_PATH}")

    settings = _settings(
        source_language, whisper_model, target_languages, voice_mode, voice_file,
        sync_mode, bitrate, generate_srt, preserve_background,
    )
    if selected_project == "__new__":
        if len(source_paths) > 1:
            projects = []
            for path in source_paths:
                project = STORE.create(Path(path).stem, path, settings)
                item_settings = STORE.persist_voice_file(project, dict(settings))
                STORE.update_settings(project, item_settings)
                projects.append(project)
            batch = BATCHES.create(project_name or "Lote de dublagem", [item["id"] for item in projects])
            yield from run_batch(batch["id"], update_batch_selector=False)
            return
        project = STORE.create(project_name, source_paths[0], settings)
        settings = STORE.persist_voice_file(project, settings)
        STORE.update_settings(project, settings)
    else:
        project = STORE.get(selected_project)
        settings = STORE.persist_voice_file(project, settings)
        STORE.update_settings(project, settings)

    if not PIPELINE_LOCK.acquire(blocking=False):
        raise gr.Error("Já existe um processamento em andamento.")

    queue: Queue[tuple[str, Any]] = Queue()

    def callback(stage: str, message: str, progress: float | None) -> None:
        queue.put(("progress", (stage, message, progress)))

    def worker() -> None:
        try:
            outputs = DubPipeline(STORE, ZAST_PATH).run(project["id"], callback)
            queue.put(("done", outputs))
        except Exception as exc:
            queue.put(("error", exc))
        finally:
            PIPELINE_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    outputs: list[str] = []
    yield status_markdown(project, "Preparando o projeto"), outputs, gr.update(choices=project_choices(), value=project["id"]), "Preparando…"

    while True:
        try:
            kind, payload = queue.get(timeout=5.0)
        except Empty:
            current = STORE.get(project["id"])
            yield status_markdown(current, "Processando… acompanhe também o terminal"), outputs, gr.update(), "Processando…"
            continue
        current = STORE.get(project["id"])
        if kind == "progress":
            _, message, progress = payload
            label = message if progress is None else f"{message} · {int(progress * 100)}%"
            yield status_markdown(current, label), outputs, gr.update(), label
        elif kind == "done":
            outputs = payload
            yield status_markdown(STORE.get(project["id"]), "Concluído"), outputs, gr.update(choices=project_choices(), value=project["id"]), "Concluído"
            return
        else:
            message = str(payload)
            yield status_markdown(current, f"Erro: {message}"), outputs, gr.update(choices=project_choices(), value=project["id"]), f"Erro: {message}"
            return


def batch_status_markdown(batch: dict[str, Any], project: dict[str, Any] | None, message: str = "") -> str:
    total = len(batch["project_ids"])
    current_index = 0
    if batch.get("current_project_id") in batch["project_ids"]:
        current_index = batch["project_ids"].index(batch["current_project_id"]) + 1
    header = f"## Lote: {batch['name']}\n**Status:** {batch['status']} · **Arquivo atual:** {current_index}/{total}"
    return f"{header}\n\n{status_markdown(project, message)}" if project else header


def run_batch(batch_id: str, update_batch_selector: bool = True) -> Iterator[tuple[str, list[str], Any, str]]:
    if not batch_id:
        raise gr.Error("Selecione um lote para retomar.")
    if not ZAST_PATH.exists():
        raise gr.Error(f"ZastTranslate não encontrado em: {ZAST_PATH}")
    if not PIPELINE_LOCK.acquire(blocking=False):
        raise gr.Error("Já existe um processamento em andamento.")

    queue: Queue[tuple[str, Any]] = Queue()

    def worker() -> None:
        all_outputs: list[str] = []
        try:
            batch = BATCHES.get(batch_id)
            batch["status"] = "running"
            batch["last_error"] = None
            BATCHES.save(batch)
            for index, project_id in enumerate(batch["project_ids"], 1):
                batch["current_project_id"] = project_id
                BATCHES.save(batch)

                def callback(stage: str, message: str, progress: float | None, *, number=index, total=len(batch["project_ids"])) -> None:
                    queue.put(("progress", (project_id, number, total, stage, message, progress)))

                all_outputs.extend(DubPipeline(STORE, ZAST_PATH).run(project_id, callback))
            batch["status"] = "completed"
            batch["current_project_id"] = None
            BATCHES.save(batch)
            queue.put(("done", sorted(set(all_outputs))))
        except Exception as exc:
            batch = BATCHES.get(batch_id)
            batch["status"] = "failed"
            batch["last_error"] = str(exc)
            BATCHES.save(batch)
            queue.put(("error", exc))
        finally:
            PIPELINE_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    yield batch_status_markdown(BATCHES.get(batch_id), None, "Preparando fila"), [], gr.update(), "Preparando lote…"

    while True:
        try:
            kind, payload = queue.get(timeout=5.0)
        except Empty:
            batch = BATCHES.get(batch_id)
            current = STORE.get(batch["current_project_id"]) if batch.get("current_project_id") else None
            yield batch_status_markdown(batch, current, "Processando… acompanhe também o terminal"), [], gr.update(), "Processando lote…"
            continue
        batch = BATCHES.get(batch_id)
        if kind == "progress":
            project_id, number, total, _, message, progress = payload
            label = f"Arquivo {number}/{total}: {message}"
            if progress is not None:
                label += f" · {int(progress * 100)}%"
            yield batch_status_markdown(batch, STORE.get(project_id), label), [], gr.update(), label
        elif kind == "done":
            selector = (
                gr.update(choices=batch_choices(), value=batch_id)
                if update_batch_selector else gr.update(choices=project_choices())
            )
            yield batch_status_markdown(batch, None, "Lote concluído"), payload, selector, "Lote concluído"
            return
        else:
            message = str(payload)
            current = STORE.get(batch["current_project_id"]) if batch.get("current_project_id") else None
            selector = (
                gr.update(choices=batch_choices(), value=batch_id)
                if update_batch_selector else gr.update(choices=project_choices())
            )
            yield batch_status_markdown(batch, current, f"Erro: {message}"), [], selector, f"Erro: {message}"
            return


def resume_batch(batch_id: str) -> Iterator[tuple[str, list[str], Any, str]]:
    yield from run_batch(batch_id, update_batch_selector=True)


CSS = """
:root { --df-accent: #7c5cff; --df-panel: #171923; }
.gradio-container { max-width: 1280px !important; }
.df-hero { padding: 24px 4px 10px; }
.df-hero h1 { font-size: 34px; letter-spacing: -1px; margin: 0; }
.df-hero p { opacity: .72; margin-top: 6px; }
.df-card { border: 1px solid rgba(255,255,255,.10) !important; border-radius: 18px !important; }
.df-run button { min-height: 52px; font-size: 17px; font-weight: 700; }
"""


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="DubForge", css=CSS, theme=gr.themes.Soft(primary_hue="violet")) as demo:
        gr.HTML("<div class='df-hero'><h1>DubForge</h1><p>Local AI Dubbing Studio · MP3 + SRT · processamento em lote com retomada automática</p></div>")
        with gr.Row():
            with gr.Column(scale=6, elem_classes="df-card"):
                with gr.Row():
                    project_select = gr.Dropdown(project_choices(), value="__new__", label="Projeto", scale=5)
                    refresh_btn = gr.Button("Atualizar lista", scale=1)
                project_name = gr.Textbox(label="Nome do projeto", placeholder="Ex.: Pregação 18-08-2026")
                source_file = gr.File(label="Vídeo ou áudios originais", type="filepath", file_count="multiple")
                with gr.Row():
                    source_language = gr.Dropdown(SOURCE_LANGUAGE_CHOICES, value="Portuguese", label="Idioma original")
                    whisper_model = gr.Dropdown(WHISPER_MODELS, value="large-v3", label="Modelo Whisper")
                target_languages = gr.CheckboxGroup(LANGUAGE_CHOICES, value=DEFAULT_TARGETS, label="Idiomas de destino")

                gr.Markdown("#### Voz e sincronização")
                with gr.Row():
                    voice_mode = gr.Radio(["Clonar voz original", "Arquivo de referência"], value="Clonar voz original", label="Voz")
                    sync_mode = gr.Radio(["Priorizar sincronismo", "Nunca cortar a fala"], value="Priorizar sincronismo", label="Sincronização")
                voice_file = gr.File(label="Referência de voz (5–30 s)", type="filepath")

                gr.Markdown("#### Saída")
                with gr.Row():
                    bitrate = gr.Dropdown(MP3_BITRATES, value="320k", label="Bitrate MP3")
                    generate_srt = gr.Checkbox(value=True, label="Gerar SRT natural")
                    preserve_background = gr.Checkbox(value=True, label="Preservar música/ambiente")
                run_btn = gr.Button("🚀 Dublar / retomar", variant="primary", elem_classes="df-run")
                with gr.Row():
                    batch_select = gr.Dropdown(batch_choices(), value="", label="Lote salvo")
                    refresh_batches_btn = gr.Button("Atualizar lotes")
                resume_batch_btn = gr.Button("▶ Retomar lote selecionado")
                live_status = gr.Textbox(label="Status atual", value="Aguardando", interactive=False)

            with gr.Column(scale=4, elem_classes="df-card"):
                status = gr.Markdown(status_markdown(None), label="Andamento")
                outputs = gr.File(label="Arquivos gerados", file_count="multiple", interactive=False)
                gr.Markdown(
                    "O DubForge salva a transcrição, cada tradução e cada segmento de áudio. "
                    "Se o computador reiniciar, selecione o mesmo projeto e clique em **Dublar / retomar**."
                )

        load_outputs = [
            project_name, source_file, source_language, whisper_model, target_languages,
            voice_mode, voice_file, sync_mode, bitrate, generate_srt,
            preserve_background, status, outputs,
        ]
        project_select.change(load_project, [project_select], load_outputs)
        refresh_btn.click(refresh_projects, outputs=[project_select])
        refresh_batches_btn.click(refresh_batches, outputs=[batch_select])
        run_btn.click(
            run_project,
            [project_select, project_name, source_file, source_language, whisper_model,
             target_languages, voice_mode, voice_file, sync_mode, bitrate,
             generate_srt, preserve_background],
            [status, outputs, project_select, live_status],
        )
        resume_batch_btn.click(
            resume_batch,
            [batch_select],
            [status, outputs, batch_select, live_status],
        )
    return demo
