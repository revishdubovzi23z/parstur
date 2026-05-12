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
- `index.html`: The current (legacy) monolithic frontend. Uses Vue 3 (CDN), Tailwind Play, and SortableJS.

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
Refer to [ROADMAP.md](ROADMAP.md) for the active development stage and priorities. We are currently finishing Stage 2 and moving into Stage 3 (Settings Migration).
