# Wellness Bots — Архитектура и техническая реализация

> Внутренний документ. Рабочая спецификация для реализации.
> Стек на 2026, монолит, минимум DevOps, два пользователя (фаундеры).

---

## 0. Принципы

1. **Time-to-Value важнее идеальной архитектуры.** Запускаем тонкий вертикальный срез, дальше итерируем.
2. **Один процесс, один деплой.** Никаких микросервисов, очередей, оркестраторов.
3. **Free-тиры — оптимизация, не фундамент.** Платный provider как baseline, free как fallback через LiteLLM.
4. **Источник правды для контента — Markdown в git-репо.** БД хранит только метаданные, дедуп-хеши, логи.
5. **Все LLM-выходы — Pydantic-модели.** Никакого regex по JSON.
6. **Любая внешняя точка отказа должна логироваться в Sentry и слать heartbeat.**

---

## 1. Высокоуровневая схема

```
                ┌─────────────────────────────────────────┐
                │         Один Telegram-бот                │
                │  (aiogram 3.x, два режима: radar/insight)│
                └────────────┬─────────────────────┬──────┘
                             │                     │
              ┌──────────────▼──────┐    ┌─────────▼──────────┐
              │  PUSH (Radar)       │    │  PULL (Insight)    │
              │  APScheduler cron   │    │  on-demand command │
              └──────────────┬──────┘    └─────────┬──────────┘
                             │                     │
                ┌────────────▼─────────────────────▼─────────┐
                │              Pipeline core                  │
                │                                             │
                │  Parsers ─→ Dedup (Postgres hash) ─→        │
                │  Pre-filter (embeddings) ─→ LLM (LiteLLM) ─→│
                │  Pydantic validation ─→ TG card / report    │
                └────────────┬─────────────────────┬─────────┘
                             │                     │
                  ┌──────────▼─────┐      ┌────────▼────────┐
                  │ Supabase (PG)  │      │ GitHub repo     │
                  │ - hashes       │      │ - approved.md   │
                  │ - run logs     │      │ - insights.md   │
                  │ - feedback     │      └─────────────────┘
                  └────────────────┘                │
                             │                      │
                             └────────► Linear API ◄┘
                                       (для Бота №2)
```

**Ключевое решение:** один бот с двумя режимами, не два. Общие хендлеры, общий деплой, общий логгер. Разделение — только по командам и role-based prompt'ам.

---

## 2. Структура проекта

```
/wellness_bots
├── bot/
│   ├── __init__.py
│   ├── handlers/
│   │   ├── radar.py            # /radar, callback'и одобрения
│   │   ├── insight.py          # /insight <url>, callback'и в Linear
│   │   └── common.py           # /start, /help, /status
│   ├── keyboards.py            # инлайн-клавиатуры
│   ├── middlewares.py          # auth (whitelist user_id), throttling
│   └── formatters.py           # рендер карточек в Markdown V2
│
├── parsers/
│   ├── base.py                 # Protocol: Parser, fetch() -> list[RawItem]
│   ├── rss.py                  # feedparser, общая обёртка
│   ├── pubmed.py               # PubMed E-utilities
│   ├── openalex.py             # OpenAlex API (замена Google Scholar)
│   ├── serper_news.py          # Serper.dev для научпопа/тревел
│   ├── reddit.py               # PRAW (asyncpraw)
│   └── youtube.py              # YouTube Data API v3
│
├── ai_engine/
│   ├── client.py               # LiteLLM router + fallbacks
│   ├── prompts/
│   │   ├── radar_filter.md     # отсев мусора
│   │   ├── radar_summary.md    # выжимка инсайта
│   │   └── insight_analyst.md  # анализ болей ЦА
│   ├── schemas.py              # Pydantic для structured output
│   ├── embeddings.py           # pre-filter через эмбеддинги
│   └── cache.py                # кэш эмбеддингов в Postgres
│
├── database/
│   ├── client.py               # asyncpg pool
│   ├── models.py               # SQLAlchemy 2.0 декларативные
│   ├── repos/
│   │   ├── dedup.py            # хеши спарсенного
│   │   ├── runs.py             # логи запусков
│   │   ├── approved.py         # одобренные карточки
│   │   └── feedback.py         # ❌ Мусор — для будущего fine-tune фильтра
│   └── migrations/             # alembic
│
├── integrations/
│   ├── linear.py               # GraphQL: createIssue
│   └── github.py               # PyGithub: commit Markdown в content-репо
│
├── scheduler/
│   ├── jobs.py                 # определения cron-задач
│   └── runner.py               # APScheduler + SQLAlchemyJobStore
│
├── observability/
│   ├── logging.py              # loguru конфиг
│   ├── sentry.py               # init
│   └── heartbeat.py            # weekly health-report в TG
│
├── config.py                   # Pydantic Settings (env)
├── main.py                     # точка входа: DI, startup, polling
├── pyproject.toml              # uv / poetry
├── Dockerfile                  # multi-stage, python:3.13-slim
├── railway.toml                # деплой-конфиг
└── tests/
    ├── test_parsers.py         # моки HTTP, проверка маппинга
    ├── test_prompts.py         # snapshot-тесты на golden inputs
    └── test_dedup.py
```

---

## 3. Стек (фиксация версий)

| Компонент | Выбор | Зачем |
|---|---|---|
| Runtime | Python 3.13 | актуальная LTS-like |
| Bot framework | `aiogram` 3.x | async, FSM, middleware |
| Async HTTP | `httpx` | таймауты, HTTP/2 |
| LLM gateway | `litellm` | router, fallbacks, retries |
| Embeddings | `gemini-embedding-001` или `text-embedding-3-small` | дешёвый pre-filter |
| Scheduler | `APScheduler` 3.x + `SQLAlchemyJobStore` | persistent jobs |
| ORM | `SQLAlchemy` 2.0 (async) + `alembic` | миграции |
| DB driver | `asyncpg` | прямой Postgres |
| Settings | `pydantic-settings` | typed env |
| Validation | `pydantic` v2 | + structured LLM output |
| Reddit | `asyncpraw` | OAuth, rate limits |
| YouTube | `google-api-python-client` (sync, в `to_thread`) | comments.list |
| RSS | `feedparser` | стандарт |
| Retries | `tenacity` | парсеры |
| Logs | `loguru` | structured |
| Errors | `sentry-sdk` | free tier |
| GitHub | `PyGithub` | commit Markdown |
| Linear | прямой GraphQL через `httpx` | createIssue |
| Tests | `pytest` + `pytest-asyncio` + `vcrpy` | моки HTTP |
| Lint/Format | `ruff` | всё в одном |
| Package mgmt | `uv` | быстрее poetry |

### LLM модели (через LiteLLM router)

```python
# ai_engine/client.py — концепт
LITELLM_ROUTER = {
    "radar_filter": [
        {"model": "gemini/gemini-2.5-flash-lite", "tier": "paid"},  # baseline
        {"model": "groq/llama-3.3-70b-versatile", "tier": "free_fallback"},
    ],
    "radar_summary": [
        {"model": "gemini/gemini-2.5-flash", "tier": "paid"},  # большой контекст для батча
        {"model": "openrouter/google/gemini-2.5-flash", "tier": "fallback"},
    ],
    "insight_analyst": [
        {"model": "anthropic/claude-haiku-4-5", "tier": "paid"},  # сильное reasoning
        {"model": "deepseek/deepseek-chat", "tier": "fallback"},
        {"model": "groq/llama-3.3-70b-versatile", "tier": "free_fallback"},
    ],
}
```

**Prompt caching:** включить для всех моделей, где поддерживается (Anthropic, Gemini). System prompt стабильный → экономия ~75% на повторных вызовах в Боте №1.

---

## 4. Модели данных (Postgres / Supabase)

```sql
-- дедупликация: одна строка на спарсенный объект
CREATE TABLE parsed_items (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,           -- 'pubmed', 'reddit', 'natgeo_rss', ...
    external_id   TEXT NOT NULL,           -- url или native id
    content_hash  TEXT NOT NULL,           -- sha256(title + body)
    parsed_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source, external_id),
    UNIQUE (content_hash)
);
CREATE INDEX ix_parsed_items_source_date ON parsed_items (source, parsed_at DESC);

-- логи запусков (отладка + heartbeat)
CREATE TABLE run_logs (
    id           BIGSERIAL PRIMARY KEY,
    job_name     TEXT NOT NULL,
    started_at   TIMESTAMPTZ DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT,                     -- 'ok', 'partial', 'error'
    items_total  INT,
    items_kept   INT,                      -- после pre-filter
    items_sent   INT,                      -- после LLM
    cost_usd     NUMERIC(10, 6),           -- из LiteLLM callback
    error        TEXT
);

-- одобренные карточки (ссылка на git-коммит)
CREATE TABLE approved_items (
    id              BIGSERIAL PRIMARY KEY,
    parsed_item_id  BIGINT REFERENCES parsed_items(id),
    summary         TEXT NOT NULL,
    insight_tags    TEXT[],
    github_commit   TEXT,                  -- sha коммита в content-репо
    approved_by     BIGINT,                -- tg user_id
    approved_at     TIMESTAMPTZ DEFAULT now()
);

-- негативный фидбек (для будущей донастройки фильтра)
CREATE TABLE feedback_trash (
    id              BIGSERIAL PRIMARY KEY,
    parsed_item_id  BIGINT REFERENCES parsed_items(id),
    reason          TEXT,                  -- опц. свободный текст из reply
    rejected_at     TIMESTAMPTZ DEFAULT now()
);

-- кэш эмбеддингов (чтобы не пересчитывать)
CREATE TABLE embedding_cache (
    content_hash  TEXT PRIMARY KEY,
    model         TEXT NOT NULL,
    vector        vector(768),             -- pgvector extension
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- эталонные эмбеддинги "интересного" (для cosine similarity)
CREATE TABLE reference_embeddings (
    id        BIGSERIAL PRIMARY KEY,
    label     TEXT NOT NULL,               -- 'neuroscience', 'transformational_travel'
    vector    vector(768) NOT NULL,
    sample    TEXT                         -- исходный текст-пример
);
```

**Расширения:** `pgvector` (Supabase поддерживает из коробки).

---

## 5. Pipeline — Бот №1 (Radar, push)

### Триггер
APScheduler, cron-выражения по источнику. По умолчанию: science 2 раза/день, travel media 1 раз/день.

### Шаги

```python
# scheduler/jobs.py — псевдокод
async def run_radar(source_group: Literal["science", "travel"]):
    run = await runs_repo.start(job_name=f"radar:{source_group}")
    try:
        # 1. Parse
        raw_items = await parsers.fetch_all(source_group)

        # 2. Dedup по content_hash
        new_items = await dedup_repo.filter_new(raw_items)
        if not new_items:
            return await runs_repo.finish(run, status="ok", items_total=0)

        # 3. Pre-filter через эмбеддинги (cosine similarity к reference)
        kept = await embeddings.prefilter(
            new_items,
            threshold=0.62,           # тюнить на golden set
            references=["neuroscience", "transformational_travel"],
        )

        # 4. Батч в LLM (Gemini Flash, окно ~1M)
        batches = chunk_by_tokens(kept, max_tokens=200_000)
        cards = []
        for batch in batches:
            result = await ai.summarize_batch(
                batch,
                prompt="radar_summary",
                schema=RadarCardList,         # Pydantic structured output
            )
            cards.extend(result.cards)

        # 5. Сохранить parsed_items + отправить карточки
        await dedup_repo.bulk_insert(new_items)
        for card in cards:
            await tg.send_card(card, keyboard=approve_or_trash_kb(card.id))

        await runs_repo.finish(run, status="ok", items_total=len(raw_items),
                               items_kept=len(kept), items_sent=len(cards))
    except Exception as e:
        sentry_sdk.capture_exception(e)
        await runs_repo.finish(run, status="error", error=str(e))
        raise
```

### Карточка в TG

```
🧠 Neuroscience · PubMed
Default Mode Network and ego dissolution
under psilocybin: replication study

Inсайт: подтверждена связь снижения активности
DMN с ощущением "растворения эго" — релевантно
для ретритов с дыхательными практиками.

🔗 Источник
─────────────────
[✅ В работу]   [❌ Мусор]
```

**Callback `approve`:**
1. Записать в `approved_items`.
2. Закоммитить `.md` файл в content-репо через GitHub API (имя: `YYYY-MM-DD-slug.md`, фронтматтер с тегами).
3. Отредактировать сообщение → "✅ Сохранено: <commit-link>".

**Callback `trash`:**
1. Записать в `feedback_trash`.
2. Удалить сообщение или схлопнуть.

---

## 6. Pipeline — Бот №2 (Insight, pull)

### Триггер
`/insight https://reddit.com/r/solotravel/comments/...` или `/insight youtube <video_url>`.

### Шаги

```python
async def run_insight(url: str, user_id: int):
    source = detect_source(url)               # reddit | youtube
    parser = PARSERS[source]

    # 1. Pull топ комментариев (depth=2, top=100)
    thread = await parser.fetch_thread(url, top_n=100)

    # 2. LLM-анализ (claude-haiku-4-5 baseline)
    report: InsightReport = await ai.analyze(
        thread,
        prompt="insight_analyst",
        schema=InsightReport,
    )

    # 3. Рендер отчёта
    await tg.send_report(report, keyboard=push_to_linear_kb(report.id))
```

### Pydantic-схема отчёта

```python
class PainPoint(BaseModel):
    category: Literal["fear", "desire", "meaning_crisis", "frustration"]
    title: str
    description: str
    representative_quotes: list[str]  # 2-3 цитаты
    frequency: int                    # сколько раз встречается

class InsightReport(BaseModel):
    source_url: str
    audience_segment: str             # "соло-путешественники 30-40, в кризисе смыслов"
    pain_points: list[PainPoint]
    desires: list[str]
    triggers: list[str]               # что заставляет искать решение
    summary_for_aeo: str              # короткий блок для answer engine optimization
```

### Кнопка "→ Linear"

GraphQL `issueCreate` с title=`AEO insight: {audience_segment}`, description=Markdown-рендер отчёта, projectId из env, labels=`["audience-research"]`. Возвращаем ссылку на issue в TG.

---

## 7. Парсеры — конкретика по источникам

| Источник | Библиотека | Auth | Rate limits | Заметки |
|---|---|---|---|---|
| **PubMed** | `httpx` → E-utilities | API key (free) | 10 req/s с ключом | XML, парсим в Pydantic |
| **OpenAlex** | `httpx` | mailto в User-Agent | мягкие | замена Google Scholar |
| **Serper.dev** | `httpx` | API key | 2.5k free | для тревел-научпопа |
| **National Geographic** | `feedparser` | — | — | проверить feed работает (часто truncated) |
| **Condé Nast Traveler** | `feedparser` | — | — | **проверить до коммита** в архитектуру |
| **Revista VIAJAR** | `feedparser` | — | — | **проверить до коммита** |
| **Reddit** | `asyncpraw` | OAuth (script app) | 100 QPM | top=100, sort=top, t=month |
| **YouTube** | `google-api-python-client` | API key | 10k units/day | `commentThreads.list` |

**Важное:** перед коммитом в архитектуру прогнать `feedparser` по всем тревел-источникам. Если RSS обрезан до заголовков — выкидывать или заменять на Serper News.

### Базовый интерфейс парсера

```python
# parsers/base.py
class RawItem(BaseModel):
    source: str
    external_id: str            # url
    title: str
    body: str
    published_at: datetime | None
    metadata: dict = {}
    content_hash: str           # вычисляется в __init__

class Parser(Protocol):
    name: str
    async def fetch(self, **kwargs) -> list[RawItem]: ...
```

---

## 8. LLM-слой — детали

### Pre-filter через эмбеддинги

```python
# ai_engine/embeddings.py — концепт
async def prefilter(items: list[RawItem], threshold: float,
                    references: list[str]) -> list[RawItem]:
    ref_vectors = await load_reference_embeddings(references)
    kept = []
    for item in items:
        vec = await get_or_compute_embedding(item.content_hash, item.title + item.body[:2000])
        max_sim = max(cosine(vec, ref) for ref in ref_vectors)
        if max_sim >= threshold:
            kept.append(item)
    return kept
```

**Тюнинг threshold:** golden set из 50 размеченных статей (manual), grid search по `[0.55, 0.58, 0.60, 0.62, 0.65]` на precision/recall. Перепроверять раз в квартал.

### Structured output

LiteLLM `response_format={"type": "json_schema", "json_schema": ...}` для всех вызовов. Pydantic-модель → JSON Schema через `model_json_schema()`.

### Retries и fallbacks

```python
# config LiteLLM Router
router = Router(
    model_list=MODEL_LIST,
    fallbacks=[
        {"radar_summary": ["radar_summary_fallback"]},
        {"insight_analyst": ["insight_analyst_fallback"]},
    ],
    num_retries=2,
    retry_after=5,
    timeout=60,
)
```

### Cost tracking

LiteLLM callback `success_callback=["custom_cost_logger"]` → пишем `cost_usd` в `run_logs`. Раз в неделю — сводка в TG.

---

## 9. Telegram UX

### Аутентификация
Whitelist `user_id` в env (`ALLOWED_USERS=12345,67890`). Middleware режет всё остальное молча.

### Команды
- `/start` — приветствие, статус ботов.
- `/help` — список команд.
- `/status` — последние run_logs (24ч), сколько найдено/отфильтровано/отправлено.
- `/insight <url>` — запуск Бота №2.
- `/radar_now <science|travel>` — ручной триггер Radar (для тестов).
- `/digest` — еженедельный дайджест costs + heartbeat источников.

### Форматирование
Markdown V2, эскейпим через `aiogram.utils.markdown`. Длинные саммари — обрезаем до 3500 символов с "...читать дальше" → callback на полный текст.

---

## 10. Scheduler — конкретика

```python
# scheduler/runner.py
scheduler = AsyncIOScheduler(
    jobstores={
        "default": SQLAlchemyJobStore(url=settings.database_url)
    },
    timezone="Europe/Madrid",
)

scheduler.add_job(
    run_radar, "cron", hour="9,18", args=["science"],
    id="radar_science", replace_existing=True,
    misfire_grace_time=3600,
)
scheduler.add_job(
    run_radar, "cron", hour="10", args=["travel"],
    id="radar_travel", replace_existing=True,
)
scheduler.add_job(
    weekly_digest, "cron", day_of_week="sun", hour="10",
    id="weekly_digest",
)
scheduler.add_job(
    heartbeat_check, "interval", hours=6,
    id="heartbeat",
)
```

`misfire_grace_time` — если процесс лежал и пропустил, выполнится при подъёме (но не больше часа отставания).

---

## 11. Обсервабилити

### Логи (loguru)
```python
logger.add(sys.stdout, format="{time} {level} {extra} {message}",
           serialize=True, level="INFO")
logger.bind(job="radar_science").info("started", items=42)
```
Railway собирает stdout, Sentry получает level=ERROR.

### Sentry
`sentry_sdk.init(dsn=..., traces_sample_rate=0.1, environment=settings.env)`. Wrapper для всех job'ов APScheduler.

### Heartbeat
Раз в 6 часов: проверка, что каждый источник возвращал >0 items за последние 24ч. Если нет — алерт в TG: "⚠️ Источник `condenast_rss` молчит 36ч".

---

## 12. Деплой

### Dockerfile (multi-stage)
```dockerfile
FROM python:3.13-slim AS builder
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.13-slim
COPY --from=builder /app/.venv /app/.venv
COPY . /app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "main"]
```

### Railway
- Один service: `bot` (polling-режим, не webhook — нет нужды в публичном URL).
- Postgres подключаем через Supabase, не Railway Postgres (бэкапы и pgvector в Supabase из коробки).
- Env vars через Railway UI.
- Healthcheck: HTTP-эндпоинт `/health` на `aiohttp` (поднимаем рядом с polling), Railway пингует.

### CI (GitHub Actions)
- `ruff check` + `ruff format --check`
- `pytest`
- `alembic upgrade head --sql` для проверки миграций

Railway сам триггерит deploy на push в main.

---

## 13. Конфиг (Pydantic Settings)

```python
class Settings(BaseSettings):
    # Telegram
    bot_token: SecretStr
    allowed_users: list[int]

    # DB
    database_url: PostgresDsn

    # LLM providers
    gemini_api_key: SecretStr
    anthropic_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None

    # Parsers
    pubmed_api_key: SecretStr | None = None
    serper_api_key: SecretStr
    reddit_client_id: SecretStr
    reddit_client_secret: SecretStr
    youtube_api_key: SecretStr

    # Integrations
    linear_api_key: SecretStr
    linear_team_id: str
    github_token: SecretStr
    github_content_repo: str             # "founder/wellness-content"

    # Observability
    sentry_dsn: SecretStr | None = None
    log_level: str = "INFO"
    env: Literal["dev", "prod"] = "prod"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
```

---

## 14. Roadmap (по фазам)

### Phase 0 — каркас (1-2 дня)
- [ ] Скелет проекта, `pyproject.toml`, ruff, pre-commit.
- [ ] aiogram polling, `/start`, whitelist middleware.
- [ ] Supabase Postgres + alembic + базовые таблицы.
- [ ] Railway деплой, healthcheck, Sentry.

### Phase 1 — Бот №1 MVP (3-5 дней)
- [ ] PubMed + 1 RSS-источник (NatGeo).
- [ ] Dedup по hash.
- [ ] LiteLLM router, Gemini Flash, structured output.
- [ ] Карточка с ✅/❌ кнопками.
- [ ] Запись approved → GitHub commit.
- [ ] APScheduler с одним cron-job'ом.

### Phase 2 — Бот №2 MVP (2-3 дня)
- [ ] `asyncpraw` парсер Reddit.
- [ ] `/insight` команда.
- [ ] InsightReport schema + claude-haiku промпт.
- [ ] Linear createIssue.

### Phase 3 — оптимизация (по необходимости)
- [ ] Embeddings pre-filter + reference set.
- [ ] Prompt caching.
- [ ] YouTube comments parser.
- [ ] Дополнительные RSS-источники (после ручной проверки feed'ов).
- [ ] `/digest` weekly.

### Phase 4 — поддержка
- [ ] Heartbeat по источникам.
- [ ] Cost tracking + бюджетные алерты.
- [ ] Snapshot-тесты на golden inputs для промптов.

---

## 15. Открытые вопросы (решить до Phase 1)

1. **Content repo на GitHub** — отдельный приватный репо или папка в этом? → отдельный, чтобы можно было кому-то отдать без кода.
2. **Тревел-источники** — какие RSS реально работают? → ручная проверка `feedparser` на каждом до Phase 1.
3. **Reference set для эмбеддингов** — 30-50 примеров "интересного" контента нужно собрать вручную.
4. **Linear team/project ID** — куда падают тикеты от Бота №2.
5. **Бюджетный лимит на LLM** — порог, после которого Sentry-алерт. Предложение: $10/мес hard cap, $5 soft alert.

---

## 16. Решённые узкие места (из ревью)

| Проблема | Решение |
|---|---|
| Free-тиры как фундамент | Paid baseline + free fallback через LiteLLM router |
| LLM на каждой статье | Embeddings pre-filter (~50× дешевле) |
| Google Scholar нестабилен | OpenAlex + Semantic Scholar |
| RSS травел-медиа может быть труncated | Ручная валидация до Phase 1, fallback на Serper News |
| APScheduler теряет джобы при рестарте | SQLAlchemyJobStore в Supabase |
| Тихая поломка парсера | Sentry + heartbeat-checker раз в 6 часов |
| Парсинг мусорного JSON от LLM | Structured output + Pydantic |
| Контент в БД → миграционный ад | Markdown в git как source of truth, Postgres только для метаданных |
| Два бота → удвоение DevOps | Один бот, два режима через команды |
| Таймауты на медленных fetch'ах | `httpx` с явными timeouts + tenacity retries |
