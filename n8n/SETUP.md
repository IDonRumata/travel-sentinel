# n8n Workflow Setup Guide

Пошаговая конфигурация n8n для Travel Sentinel.

---

## Шаг 1: Импорт Workflow

1. Открыть n8n (http://localhost:5678 или твой IP:5679)
2. **Menu** (левое меню) → **Workflows**
3. **+ Create New** → **Import from file**
4. Выбрать файл: `n8n/travel_sentinel_workflow.json`
5. **Import**

Workflow появится в списке с названием "Travel Sentinel - AI Agent".

---

## Шаг 2: Добавление Credentials

### 2.1 Anthropic API (Claude)

1. Открыть workflow
2. Слева: **Settings** → **Credentials**
3. **+ Create New** → Выбрать **Anthropic**
4. Заполнить:
   - **Display name:** Anthropic (или как угодно)
   - **API Key:** твой `ANTHROPIC_API_KEY` (из `.env`)
5. **Save**

### 2.2 Telegram Bot

1. **+ Create New** → Выбрать **Telegram Bot API**
2. Заполнить:
   - **Display name:** Telegram (или как угодно)
   - **Bot Token:** твой `TELEGRAM_BOT_TOKEN` (из `.env`)
3. **Save**

---

## Шаг 3: Обновление Узлов Workflow

Открыть workflow и обновить references на свои credentials:

### 3.1 Узел "Claude 3.5 Sonnet"

1. Двойной клик на узел
2. **Change** (напротив "Anthropic") → выбрать свой Anthropic credential
3. **Save Node**

### 3.2 Узел "Send to Telegram"

1. Двойной клик на узел
2. **Change** (напротив "Telegram Bot API") → выбрать свой Telegram credential
3. **Save Node**

### 3.3 Узел "Every 6 Hours" (Schedule)

1. Двойной клик на узел
2. **Repeat:** Every → **6** → **hours**
3. **Save Node**

Если хочешь чаще (для тестирования): **5 minutes**

---

## Шаг 4: Проверка API URL

Узлы **Search Deals Tool**, **Visa Check Tool**, **Price Drops Tool** должны указывать на твой Travel Sentinel API.

1. Открыть каждый узел (например, "Search Deals Tool")
2. **URL:** должен быть `http://localhost:8100/tools/search-deals` (локально)
   - Или `http://your.server.ip:8100/tools/search-deals` (production)
   - Или `http://api:8100/tools/search-deals` (если всё в Docker на одной сети)

---

## Шаг 5: Тестирование Workflow

### 5.1 Выполнить вручную

1. Открыть workflow
2. Кнопка **Execute Workflow** (или Ctrl+Enter)
3. Смотреть результаты в **Output** (справа)

Ожидаемо:
- Workflow запустится
- "Every 6 Hours" → триггер выполнится
- Claude AI Agent получит инструкцию
- Будут вызваны tools (search-deals, check-visa)
- Результат отправится в Telegram

### 5.2 Проверить Telegram

Если всё работает, в твой Telegram чат должно прийти сообщение типа:

```
✅ Turkey, Antalya
- Dates: 2026-05-15 - 2026-05-22
- Price: $320/person
- Hotel: 4 stars
- Visa: visa-free (30 days)
- Link: https://aviasales.ru/...

⚠️ Egypt, Hurghada
- Dates: 2026-05-20 - 2026-05-27
- Price: $280/person
- Hotel: 5 stars
- Visa: visa-on-arrival (verify before booking!)
- Link: ...
```

---

## Шаг 6: Активирование Автозапуска

1. Вверху-справа: переключатель **Activate**
2. Переключить на **ON** (синий)
3. Workflow теперь запускается каждые 6 часов автоматически

---

## Мониторинг Workflow

### Смотреть историю запусков

1. **Menu** → **Executions**
2. Выбрать твой workflow
3. Видишь список всех запусков с результатами

### Смотреть логи API в n8n

Если API вернул ошибку:
1. Открыть execution
2. Клик на узел (например "Search Deals Tool")
3. **Output** → увидишь ответ API (error code, message)

---

## Troubleshooting n8n

| Проблема | Решение |
|----------|---------|
| `Cannot GET /tools/search-deals` | API не запущен. Проверить: `curl http://localhost:8100/health` |
| `Connection timeout` | API адрес неправильный. Если Docker: использовать `http://host.docker.internal:8100` на Mac/Windows |
| Telegram не получает сообщения | Проверить `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`. Отправить `/start` боту. |
| Claude вернул ошибку 401 | `ANTHROPIC_API_KEY` неправильный или истёк. Проверить в console.anthropic.com |
| Workflow зависает | API медленно отвечает. Увеличить timeout в узле (HTTP timeout → 120 сек) |
| Нет ошибки, но и сообщения нет | Проверить узел "Has Deals?" - может быть фильтр отсекает результаты |

---

## Customization

### Изменить расписание

1. Открыть workflow
2. Узел "Every 6 Hours"
3. Изменить интервал (для тестирования: 5 minutes, для production: 24 hours)

### Изменить промпт агента

1. Открыть узел "Claude AI Agent"
2. Вкладка "Prompt" → редактировать текст
3. Добавить свои инструкции (например, фильтровать только пляжные курорты)

Пример:
```
...
ADDITIONAL RULES:
- Only recommend beach destinations (Turkey, Egypt, Maldives)
- Skip mountain resorts
- Prioritize all-inclusive deals
...
```

### Изменить фильтр Telegram

1. Открыть узел "Has Deals?"
2. Условие: `$json.output.length > 0`
3. Изменить на свою логику (например, `$json.output.length > 5` - только если >5 туров)

---

## Next Steps

1. ✅ Workflow импортирован и тестирован
2. ✅ Credentials добавлены
3. ✅ Автозапуск активирован
4. → Смотреть результаты в Telegram каждые 6 часов
5. → Настроить дашборд для monitoring (Phase 2)

Готово! 🎉
