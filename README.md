# Travel Sentinel

AI-агент для мониторинга дешёвых туров, авиабилетов и круизов для граждан Беларуси.

## Стек

- **Оркестрация:** n8n (cron + AI Agent node)
- **Мозг:** Claude 3.5 Sonnet (через n8n)
- **API:** FastAPI + asyncio
- **БД:** PostgreSQL (история цен, кеш виз)
- **Скрейперы:** Aviasales API, Travelata
- **Визовая разведка:** Brave Search API + кеш

## Параметры поиска

- **Пассажиры:** 2 взрослых, белорусские паспорта
- **Бюджет:** до $400 на человека
- **Вылет из:** MSQ (Минск), SVO/VKO/DME (Москва), LED (СПб)

## Быстрый старт

```bash
cp .env.example .env
# Заполнить .env реальными ключами

docker compose up -d
# API доступен на http://localhost:8100
# Импортировать n8n/travel_sentinel_workflow.json в n8n
```

## API Endpoints (инструменты для n8n)

| Метод | URL | Описание |
|-------|-----|----------|
| POST | /tools/search-deals | Запуск скрейперов, поиск дешёвых предложений |
| POST | /tools/check-visa | Проверка визовых требований для страны |
| GET | /tools/price-drops | Найти предложения с падением цены |
| GET | /tools/cheapest | Самые дешёвые из кеша |
| GET | /health | Проверка работоспособности |

## Структура

```
travel-sentinel/
  src/
    api/          - FastAPI endpoints
    db/           - PostgreSQL repositories
    models/       - Pydantic v2 schemas
    scrapers/     - Aviasales, Travelata scrapers
    visa/         - Visa intelligence layer
  n8n/            - n8n workflow JSON
  sql/            - Database migrations
  tests/          - Test suite
```
