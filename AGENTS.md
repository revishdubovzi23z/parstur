# Guidance for AI Agents (Antigravity/Parsclode)

This document provides essential context for AI coding assistants working on this repository.

## Project Overview
**Antigravity Tracker** (internal name `par2`) is a media manager and tracker integration for Rutor, HDRezka, and other sources.

## Codebase Structure (Domain Domains)
- `main.py`: The entry point. Contains the FastAPI app, middleware, lifecycle hooks, and most API routes (to be split in Stage 7).
- `db.py`: Database access layer (SQLite). Handles schema initialization and migrations.
- `settings.py`: Centralized configuration management using Pydantic Settings. Use `from settings import settings`.
- `*_client.py`: Clients for external APIs (TMDB, Kinopoisk, PoiskKino).
- `*_sync.py`: Logic for synchronizing media data and collections in the background.
- `frontend/`: Vite/Vue 3/TS SPA. Builds to `frontend/dist/`, which `main.py` mounts at `/`. The legacy monolithic `index.html` (Vue 3 CDN + Tailwind Play + SortableJS) was retired in ROADMAP Stage 10.7z; do not reintroduce CDN-served Vue / Tailwind / Sortable.

## Workflow & Hygiene
- **Formatting**: Always run `ruff format .` before committing.
- **Linting**: Ensure `ruff check .` passes.
- **Testing**: Verify changes with `pytest -q`. The goal is 100% pass rate.
- **Pre-commit**: Use `pre-commit run --all-files` to validate the entire repository state.

## Critical Rules
1. **No Direct DB Mutations**: Never modify the database schema directly in `db.py` without adding a corresponding SQL migration in `migrations/`.
2. **Persistence**: Do not delete `app_data.db` or `api_cache.db` unless explicitly instructed.
3. **Branching**: Do not force-push to `main`. Use PRs for significant changes.
4. **Configuration**: Use `settings.py` for all environment-based configuration. Avoid direct `os.getenv` calls in new code.

## Current Roadmap
Refer to [ROADMAP.md](ROADMAP.md) for the active development stage and priorities. Stages 0–9 are complete. Stage 10 (Vite + Vue 3 + TypeScript frontend migration) is finished: the SPA in `frontend/dist` is now served at `/` and the legacy `index.html` has been removed (Stage 10.7z).
