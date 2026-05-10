# Welness Bot

Внутренний Telegram-бот для фаундеров травел/wellness-проекта.
Два режима: **Radar** (push сбор статей и исследований) и **Insight** (pull анализ ЦА из Reddit/YouTube).

Полная архитектура: [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Phase 0 — каркас (текущее состояние)

Готово:
- Скелет проекта (config, main, healthcheck, whitelist middleware).
- Postgres подключение через SQLAlchemy + alembic.
- loguru-логи + Sentry.
- Dockerfile + railway.toml для деплоя.

Не готово (Phase 1+):
- Парсеры (PubMed, RSS, Reddit, YouTube).
- LLM-pipeline (Claude Haiku).
- Хендлеры `/radar`, `/insight`.
- GitHub-коммиты одобренного контента.

---

## Локальный запуск

```bash
python -m venv .venv
.venv/Scripts/activate     # Windows
# source .venv/bin/activate  # macOS/Linux

pip install -e ".[dev]"
cp .env.example .env       # заполнить значения
alembic upgrade head
python -m main
```

## Railway-деплой

1. **Postgres** в проекте уже создан.
2. **Bot service** подключён к этому репо, ветке `main`.
3. В Variables сервиса Bot нужно добавить:
   - `BOT_TOKEN`
   - `ALLOWED_USERS=1041468382,285949691`
   - `ANTHROPIC_API_KEY`
   - `GITHUB_TOKEN`
   - `GITHUB_CONTENT_REPO=rychagovantonv-cloud/wellness-content`
   - `SENTRY_DSN` (после регистрации на sentry.io)
   - `DATABASE_URL` → ссылка-шаблон `${{Postgres.DATABASE_URL}}` (Railway сам подставит)

   ⚠️ **Важно:** Railway по умолчанию даёт `postgresql://...`, нам нужен `postgresql+asyncpg://...`.
   В Railway добавьте переменную `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` и проверьте,
   что приложение умеет переписывать схему (см. `database/client.py`).

4. После push в `main` Railway соберёт Docker и задеплоит автоматически.

## Команды бота (Phase 0)

- `/start` — приветствие, пинг.
- `/help` — список команд.
- `/status` — последние запуски (пока пусто, появится в Phase 1).

## Структура

```
.
├── bot/              # aiogram handlers, middlewares
├── database/         # SQLAlchemy models, alembic
├── observability/    # logging, sentry
├── config.py         # Pydantic Settings
├── main.py           # entrypoint
├── Dockerfile
├── railway.toml
└── pyproject.toml
```

Парсеры, AI engine, scheduler, integrations — добавятся в Phase 1+.
