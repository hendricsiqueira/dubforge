from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import gradio as gr

from .catalog import (
    DEFAULT_TARGETS,
    LANGUAGE_CHOICES,
    MP3_BITRATES,
    SOURCE_LANGUAGE_CHOICES,
    WHISPER_MODELS,
)
from .jobs import JobManager
from .pipeline import metrics_markdown, status_markdown
from .store import BatchStore, ProjectStore

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = Path(os.environ.get("DUBFORGE_PROJECTS_DIR", APP_ROOT / "projects")).resolve()
ZAST_PATH = Path(os.environ.get("ZAST_TRANSLATE_PATH", APP_ROOT.parent / "ZastTranslate")).resolve()
STORE = ProjectStore(PROJECTS_ROOT)
BATCHES = BatchStore(PROJECTS_ROOT / "batches")
JOBS = JobManager(STORE, BATCHES, ZAST_PATH, PROJECTS_ROOT / "jobs")


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
    preserve_background: bool, gpu_hourly_usd: float, usd_brl: float,
    competitor_brl_per_minute: float,
    adaptive_mode: str = "Automático",
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
        "gpu_hourly_usd": float(gpu_hourly_usd or 0.0),
        "usd_brl": float(usd_brl or 0.0),
        "competitor_brl_per_minute": float(competitor_brl_per_minute or 2.0),
        "llm_backend": "Qwen2.5-7B-Instruct",
        "tts_backend": "VoxCPM 2",
        "adaptive_mode": adaptive_mode,
    }


def _format_seconds(value: float | None) -> str:
    if value is None:
        return "calculando…"
    total = max(0, round(float(value)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def job_markdown(job: dict[str, Any] | None) -> str:
    if not job:
        return "Nenhum trabalho iniciado."
    status_labels = {
        "pending": "⚪ Na fila", "running": "🔵 Em execução",
        "pause_requested": "🟡 Pausando", "paused": "⏸️ Pausado",
        "waiting_for_disk": "🟠 Aguardando espaço", "cancelling": "🟡 Cancelando",
        "cancelled": "⚫ Cancelado", "completed": "✅ Concluído", "failed": "🔴 Falhou",
    }
    overall = float(job.get("overall_progress", 0.0))
    stage_progress = float(job.get("stage_progress", 0.0))
    resource = job.get("resource", {})
    adaptive = job.get("adaptive", {})
    lines = [
        f"## {job.get('name', 'Trabalho')}",
        f"**Status:** {status_labels.get(job.get('status'), job.get('status'))}",
        f"**Progresso geral estimado:** {overall * 100:.1f}%",
        f"**Etapa atual:** {job.get('stage', '—')} · {stage_progress * 100:.1f}%",
        f"**Agora:** {job.get('message', '—')}",
    ]
    if job.get("current_language"):
        lines.append(f"**Idioma:** {job['current_language']}")
    if job.get("items_total"):
        lines.append(f"**Itens:** {job.get('items_done', 0)}/{job['items_total']}")
    lines.extend([
        f"**Tempo restante estimado:** {_format_seconds(job.get('eta_seconds'))}",
        "",
        "### Recursos e modo automático",
        "| Recurso | Estado |",
        "|---|---:|",
        f"| GPU | {resource.get('gpu_name') or 'não detectada'} |",
        f"| Uso da GPU | {resource.get('gpu_utilization', 0) or 0:.0f}% |",
        f"| VRAM | {resource.get('gpu_vram_used_gb', 0) or 0:.1f}/{resource.get('gpu_vram_total_gb', 0) or 0:.1f} GB |",
        f"| RAM disponível | {resource.get('ram_available_gb', 0) or 0:.1f} GB |",
        f"| Disco livre | {resource.get('disk_free_gb', 0) or 0:.1f} GB |",
        f"| Trabalhadores VoxCPM | {adaptive.get('tts_workers', 1)} |",
        f"| Lote Qwen | {adaptive.get('translation_batch_size', 8)} |",
    ])
    project_id = job.get("current_project_id")
    if project_id:
        try:
            project = STORE.get(project_id)
            progress = project.get("progress", {})
            stages = progress.get("stages", {})
            if stages:
                lines.extend(["", "### Medidas do projeto", "| Etapa | Concluído | Medida |", "|---|---:|---:|"])
                labels = {
                    "preparation": "Preparação", "separation": "Demucs",
                    "transcription": "WhisperX", "translation": "Tradução",
                    "dubbing": "Dublagem", "export": "Mixagem/MP3",
                }
                for key in ["preparation", "separation", "transcription", "translation", "dubbing", "export"]:
                    item = stages.get(key)
                    if not item:
                        continue
                    lines.append(
                        f"| {labels[key]} | {float(item.get('progress', 0)) * 100:.1f}% | "
                        f"{item.get('done', 0):.0f}/{item.get('total', 0):.0f} |"
                    )
            lines.extend(["", metrics_markdown([project])])
        except (FileNotFoundError, OSError):
            pass
    if job.get("last_error"):
        lines.extend(["", f"**Erro:** `{job['last_error']}`"])
    return "\n".join(lines)


def _job_outputs(job: dict[str, Any] | None) -> list[str]:
    if not job:
        return []
    paths = list(job.get("outputs", []))
    project_id = job.get("current_project_id")
    if project_id:
        paths.extend(str(path) for path in STORE.project_dir(project_id).glob("outputs/**/*") if path.is_file())
    if job.get("kind") == "batch":
        try:
            batch = BATCHES.get(job["target_id"])
            for item_id in batch["project_ids"]:
                paths.extend(str(path) for path in STORE.project_dir(item_id).glob("outputs/**/*") if path.is_file())
        except FileNotFoundError:
            pass
    return sorted(set(paths))


def poll_job(job_id: str | None):
    try:
        job = JOBS.jobs.get(job_id) if job_id else JOBS.latest()
    except FileNotFoundError:
        job = JOBS.latest()
    if not job:
        return job_markdown(None), [], "Aguardando", ""
    return job_markdown(job), _job_outputs(job), job.get("message", ""), job["id"]


def pause_job(job_id: str):
    if not job_id:
        raise gr.Error("Nenhum trabalho ativo.")
    JOBS.pause(job_id)
    return poll_job(job_id)


def resume_job(job_id: str):
    if not job_id:
        raise gr.Error("Nenhum trabalho selecionado.")
    JOBS.resume(job_id)
    return poll_job(job_id)


def cancel_job(job_id: str):
    if not job_id:
        raise gr.Error("Nenhum trabalho ativo.")
    JOBS.cancel(job_id)
    return poll_job(job_id)


def load_project(project_id: str):
    if not project_id or project_id == "__new__":
        return (
            "", None, "Portuguese", "large-v3", DEFAULT_TARGETS,
            "Clonar voz original", None, "Priorizar sincronismo", "320k",
            True, True, 0.0, 5.50, 2.0, status_markdown(None), [],
        )
    project = STORE.get(project_id)
    settings = project["settings"]
    output_files = [str(path) for path in STORE.project_dir(project_id).glob("outputs/**/*") if path.is_file()]
    return (
        project["name"], None, settings["source_language"], settings["whisper_model"],
        settings["target_languages"], settings["voice_mode"], settings.get("voice_file"),
        "Nunca cortar a fala" if settings["never_cut"] else "Priorizar sincronismo",
        settings["bitrate"], settings["generate_srt"], settings["preserve_background"],
        settings.get("gpu_hourly_usd", 0.0), settings.get("usd_brl", 5.50),
        settings.get("competitor_brl_per_minute", 2.0),
        status_markdown(project), output_files,
    )


def refresh_projects():
    return gr.update(choices=project_choices())


def refresh_batches():
    return gr.update(choices=batch_choices())


def enqueue_project_job(
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
    gpu_hourly_usd: float,
    usd_brl: float,
    competitor_brl_per_minute: float,
    adaptive_mode: str,
):
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
        sync_mode, bitrate, generate_srt, preserve_background, gpu_hourly_usd,
        usd_brl, competitor_brl_per_minute, adaptive_mode,
    )
    if selected_project == "__new__" and len(source_paths) > 1:
        projects = []
        for path in source_paths:
            project = STORE.create(Path(path).stem, path, settings)
            item_settings = STORE.persist_voice_file(project, dict(settings))
            STORE.update_settings(project, item_settings)
            projects.append(project)
        batch = BATCHES.create(project_name or "Lote de dublagem", [item["id"] for item in projects])
        job = JOBS.submit_batch(batch["id"], adaptive_mode)
        return job_markdown(job), [], gr.update(choices=project_choices()), job["message"], job["id"]

    if selected_project == "__new__":
        project = STORE.create(project_name, source_paths[0], settings)
    else:
        project = STORE.get(selected_project)
    settings = STORE.persist_voice_file(project, settings)
    STORE.update_settings(project, settings)
    job = JOBS.submit_project(project["id"], adaptive_mode)
    return (
        job_markdown(job), _job_outputs(job),
        gr.update(choices=project_choices(), value=project["id"]),
        job["message"], job["id"],
    )


def enqueue_batch_job(batch_id: str, adaptive_mode: str):
    if not batch_id:
        raise gr.Error("Selecione um lote para retomar.")
    job = JOBS.submit_batch(batch_id, adaptive_mode)
    return (
        job_markdown(job), _job_outputs(job),
        gr.update(choices=batch_choices(), value=batch_id),
        job["message"], job["id"],
    )


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
        active_job = gr.State("")
        timer = gr.Timer(value=2.0, active=True)
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
                gr.Markdown("#### Custos estimados")
                with gr.Row():
                    gpu_hourly_usd = gr.Number(value=0.0, label="Custo total Vast (US$/hora)", minimum=0)
                    usd_brl = gr.Number(value=5.50, label="Cotação US$ → R$", minimum=0)
                    competitor_brl_per_minute = gr.Number(value=2.0, label="Panda (R$/min por idioma)", minimum=0)
                adaptive_mode = gr.Radio(
                    ["Automático", "Econômico", "Equilibrado", "Máximo"],
                    value="Automático",
                    label="Desempenho",
                    info="Automático ajusta Qwen e trabalhadores VoxCPM conforme VRAM, RAM e disco.",
                )
                run_btn = gr.Button("🚀 Dublar / retomar", variant="primary", elem_classes="df-run")
                with gr.Row():
                    batch_select = gr.Dropdown(batch_choices(), value="", label="Lote salvo")
                    refresh_batches_btn = gr.Button("Atualizar lotes")
                resume_batch_btn = gr.Button("▶ Retomar lote selecionado")
                with gr.Row():
                    pause_btn = gr.Button("⏸ Pausar")
                    continue_btn = gr.Button("▶ Continuar")
                    cancel_btn = gr.Button("⏹ Cancelar")
                live_status = gr.Textbox(label="Status atual", value="Aguardando", interactive=False)

            with gr.Column(scale=4, elem_classes="df-card"):
                status = gr.Markdown(job_markdown(JOBS.latest()), label="Andamento")
                outputs = gr.File(label="Arquivos gerados", file_count="multiple", interactive=False)
                gr.Markdown(
                    "A página pode ser fechada sem interromper o trabalho. Ao abrir novamente, "
                    "o painel se reconecta ao gerenciador persistente. O DubForge preserva cada checkpoint."
                )

        load_outputs = [
            project_name, source_file, source_language, whisper_model, target_languages,
            voice_mode, voice_file, sync_mode, bitrate, generate_srt,
            preserve_background, gpu_hourly_usd, usd_brl, competitor_brl_per_minute,
            status, outputs,
        ]
        project_select.change(load_project, [project_select], load_outputs)
        refresh_btn.click(refresh_projects, outputs=[project_select])
        refresh_batches_btn.click(refresh_batches, outputs=[batch_select])
        run_btn.click(
            enqueue_project_job,
            [project_select, project_name, source_file, source_language, whisper_model,
             target_languages, voice_mode, voice_file, sync_mode, bitrate,
             generate_srt, preserve_background, gpu_hourly_usd, usd_brl,
             competitor_brl_per_minute, adaptive_mode],
            [status, outputs, project_select, live_status, active_job],
        )
        resume_batch_btn.click(
            enqueue_batch_job,
            [batch_select, adaptive_mode],
            [status, outputs, batch_select, live_status, active_job],
        )
        pause_btn.click(
            pause_job, [active_job], [status, outputs, live_status, active_job], show_progress="hidden"
        )
        continue_btn.click(
            resume_job, [active_job], [status, outputs, live_status, active_job], show_progress="hidden"
        )
        cancel_btn.click(
            cancel_job, [active_job], [status, outputs, live_status, active_job], show_progress="hidden"
        )
        timer.tick(
            poll_job, [active_job], [status, outputs, live_status, active_job], show_progress="hidden"
        )
        demo.load(
            poll_job, [active_job], [status, outputs, live_status, active_job], show_progress="hidden"
        )
    return demo
