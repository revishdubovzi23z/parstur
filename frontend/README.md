# `frontend/` — новый SPA (Vite + Vue 3 + TypeScript)

Этот каталог содержит **новую** версию пользовательского интерфейса
Antigravity Tracker. Пока что (ROADMAP Этап 10.1) — это только каркас:
маршруты, авторизация и весь функционал по-прежнему живут в легаси-файле
`index.html` в корне репозитория, который FastAPI отдаёт на `/`.

Новый бандл монтируется параллельно на `/beta` — это позволяет вести
работу над миграцией, не выключая работающий продукт. Дефолтный роут
переключится на новый SPA только в PR-е ROADMAP 10.7, после того как
будет достигнут полный функциональный паритет.

## Структура

- `index.html` — Vite-точка входа.
- `src/main.ts` — bootstrap Vue + Pinia.
- `src/App.vue` — заглушка «Coming soon».
- `src/style.css` — Tailwind (PostCSS, никакого Tailwind Play / CDN).
- `vite.config.ts` — `base: '/beta/'`, плагин Vue.
- `tsconfig.json` / `tsconfig.node.json` — TypeScript 5, strict.
- `tailwind.config.js` / `postcss.config.js` — Tailwind 3.4.

## Команды разработчика

```bash
cd frontend
npm ci             # установка зависимостей
npm run dev        # Vite dev-сервер на http://localhost:5173 (HMR)
npm run build      # продакшен-сборка в frontend/dist
npm run type-check # vue-tsc --noEmit
```

`npm run build` запускает `vue-tsc --noEmit` перед `vite build`, поэтому
ошибки типов ломают сборку — то же самое делает CI-джоба
`frontend-build`.

## Маунт в FastAPI

`main.py` смотрит, существует ли каталог `frontend/dist`. Если да —
монтирует его на `/beta` через `StaticFiles(..., html=True)`. Если нет
(например, репо без сборки) — `/beta` тихо отсутствует, легаси-фронт на
`/` работает как и раньше.

В Docker-образе сборка фронтенда выполняется на отдельной билд-стадии
(`node:20-alpine`) и итоговый `dist` копируется в рантайм-стадию рядом с
исходниками.
