"""Runtime support modules for the FastAPI app.

This package was introduced when `main.py` was decomposed into smaller
focused modules. The split removes the previous `import main` cycle
that every router relied on:

- ``runtime.ws``       — WebSocket connection manager and thread-safe
                         cross-loop broadcaster.
- ``runtime.rezka``    — HdRezka session lifecycle (login, dead-session
                         detection, transparent re-login, folders cache,
                         URL recovery, page fetch helper).
- ``runtime.processes``— Background-process registry and runner
                         (``run_script*`` / ``run_pipeline_task``),
                         the ``TaskQueue`` worker, status / log-file
                         tables, the ``check_any_running`` guard.
- ``runtime.admin``    — Misc administrative singletons (one-time
                         reset tokens, restart trigger).

Each module owns its own state and exposes plain module-level
attributes / functions; nothing in here should import ``main``.
"""
