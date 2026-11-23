from pathlib import Path

from dubforge.ui import PROJECTS_ROOT, build_ui

if __name__ == "__main__":
    app = build_ui()
    # UI requests are lightweight: the persistent JobManager owns the actual
    # GPU queue. A few concurrent requests let timers and controls remain
    # responsive while a user uploads or starts another queued project.
    app.queue(default_concurrency_limit=4).launch(
        server_name="127.0.0.1",
        server_port=7861,
        inbrowser=True,
        allowed_paths=[str(Path(PROJECTS_ROOT).resolve())],
        show_error=True,
    )
