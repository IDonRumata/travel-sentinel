# Setup Guide - Travel Sentinel

Полная инструкция для локальной разработки и тестирования.

## Требования

- Docker + Docker Compose (самый простой вариант)
- ИЛИ: Python 3.11+, PostgreSQL 14+, n8n (для ручной установки)
- Git

---

## Вариант 1: Docker Compose (Рекомендуется)

### 1.1 Клонирование репозитория

```bash
git clone https://github.com/IDonRumata/travel-sentinel.git
cd travel-sentinel
```

### 1.2 Заполнение `.env`

```bash
cp .env.example .env
```

Открыть `.env` и заполнить **ВСЕ** значения:

```env
# PostgreSQL (оставить как есть для docker-compose)
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=travel_sentinel
POSTGRES_USER=sentinel
POSTGRES_PASSWORD=strong_password_here

# ОБЯЗАТЕЛЬНЫЕ КЛЮЧИ
ANTHROPIC_API_KEY=sk-ant-...  # https://console.anthropic.com/account/keys
BRAVE_SEARCH_API_KEY=BSA-...   # https://api.search.brave.com/
AVIASALES_TOKEN=...            # https://support.travelpayouts.com (partner API)
TELEGRAM_BOT_TOKEN=...         # От BotFather (@BotFather /newbot)
TELEGRAM_CHAT_ID=123456789     # Число без кавычек!

# Опционально (по умолчанию OK)
LOG_LEVEL=INFO
SCRAPE_INTERVAL_MINUTES=60
MAX_PRICE_PER_PERSON_USD=400
ADULTS=2
PASSPORT_TYPE=BY
```

**ВАЖНО:** Не коммитить `.env`! Он в `.gitignore`.

### 1.3 Запуск

```bash
docker compose up -d
```

Это запустит:
- **PostgreSQL** (port 5432) - автоматически создаст schema из `sql/001_init.sql`
- **FastAPI** (port 8100) - Travel Sentinel API

### 1.4 Проверка

```bash
# Проверить что API живо
curl http://localhost:8100/health

# Ответ должен быть:
# {"status":"ok","service":"travel-sentinel",...}

# Смотреть логи API
docker compose logs -f api

# Смотреть логи БД
docker compose logs -f db
```

### 1.5 Остановка

```bash
docker compose down
# или сохранить данные БД:
docker compose down -v  # удалить volumes (данные)
docker compose down     # сохранить данные
```

---

## Вариант 2: Локальный запуск (без Docker)

Если Docker недоступен.

### 2.1 Установка PostgreSQL

```bash
# macOS
brew install postgresql@14
brew services start postgresql@14

# Linux (Ubuntu/Debian)
sudo apt install postgresql-14 postgresql-contrib-14

# Windows
# Скачать с https://www.postgresql.org/download/windows/
```

### 2.2 Создание БД и пользователя

```bash
psql -U postgres

CREATE DATABASE travel_sentinel;
CREATE USER sentinel WITH PASSWORD 'your_strong_password';
GRANT ALL PRIVILEGES ON DATABASE travel_sentinel TO sentinel;
\c travel_sentinel
\i /path/to/sql/001_init.sql
\q
```

### 2.3 Python окружение

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate      # Windows

pip install -e .
pip install -e ".[dev]"
```

### 2.4 Запуск API

```bash
export POSTGRES_HOST=localhost  # или set на Windows
python src/main.py
# API будет доступен на http://localhost:8100
```

---

## Вариант 3: n8n + Travel Sentinel

### 3.1 Запуск n8n в Docker

```bash
docker run -it --rm \
  --name n8n \
  -p 5678:5678 \
  -e DB_POSTGRESDB_DATABASE=n8n \
  -e DB_POSTGRESDB_USER=n8n \
  -e DB_POSTGRESDB_PASSWORD=n8n \
  n8nio/n8n
```

Откроется http://localhost:5678

### 3.2 Импорт workflow

1. Открыть n8n
2. Menu → Import from file
3. Выбрать `n8n/travel_sentinel_workflow.json`

### 3.3 Добавление credentials

1. Settings → Credentials → New
2. **Anthropic API:**
   - Type: Anthropic
   - API Key: (твой ANTHROPIC_API_KEY)
3. **Telegram:**
   - Type: Telegram Bot API
   - Bot Token: (твой TELEGRAM_BOT_TOKEN)

### 3.4 Обновление workflow узлов

1. Открыть импортированный workflow
2. Узел "Claude 3.5 Sonnet" → Change credential → выбрать свой Anthropic credential
3. Узел "Telegram" → Change credential → выбрать свой Telegram credential

---

## Тестирование API вручную

```bash
# Поиск туров
curl -X POST http://localhost:8100/tools/search-deals \
  -H "Content-Type: application/json" \
  -d '{"max_price": 400}'

# Проверка визы
curl -X POST http://localhost:8100/tools/check-visa \
  -H "Content-Type: application/json" \
  -d '{
    "country_code": "TR",
    "country_name": "Turkey",
    "transit_countries": []
  }'

# Найти падения цен
curl http://localhost:8100/tools/price-drops?threshold=10

# Статистика
curl http://localhost:8100/ops/stats

# Golden tests (проверка качества VisaChecker)
curl -X POST http://localhost:8100/ops/golden-tests
```

---

## Troubleshooting

| Проблема | Решение |
|----------|---------|
| `Connection refused :5432` | `docker compose ps` → проверить что `db` running. Если нет: `docker compose up -d db` |
| `POSTGRES_PASSWORD not set` | Заполнить `.env` из `.env.example` |
| `403 Brave Search` | Проверить `BRAVE_SEARCH_API_KEY` в `.env` |
| n8n не видит API | Если n8n в Docker, использовать `http://host.docker.internal:8100` вместо `localhost` |
| `ModuleNotFoundError: No module named 'src'` | `pip install -e .` (точка в конце важна!) |

---

## Следующие шаги

1. ✅ Сейчас: локальный запуск + тестирование API
2. ➜ Подключить n8n + настроить credentials
3. ➜ Запустить workflow вручную (Execute Workflow)
4. ➜ Активировать schedule (Every 6 Hours)
5. ➜ Смотреть результаты в Telegram

See: [DEPLOY.md](DEPLOY.md) для production на VPS.
