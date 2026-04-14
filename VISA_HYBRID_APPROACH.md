# Visa Hybrid Approach - Cost Optimization

## Проблема

Brave Search API платный: $5-100/месяц в зависимости от объёма запросов.

## Решение: Трёхуровневый гибридный кеш

```
Запрос на проверку визы для Turkey
  │
  ├─► Level 1: БД кеш (24h TTL)
  │     └─ FOUND: вернуть → СТОИМОСТЬ: $0
  │
  ├─► Level 2: Локальная таблица KNOWN_VISA_FREE_BY (30+ стран)
  │     └─ FOUND: вернуть + сохранить в БД → СТОИМОСТЬ: $0
  │
  └─► Level 3: Brave Search (только если unknown + ключ есть)
        └─ CALL API → СТОИМОСТЬ: $0.01-0.05
```

## Содержимое KNOWN_VISA_FREE_BY (30 стран)

Все данные кешируются локально, ZERO API-вызовов:

### CIS (стабильно)
- RU: 90 дней
- AM: 180 дней
- AZ, KG, KZ, MD, TJ, UZ: 90 дней

### Европа (Balkans)
- Serbia, Montenegro, Bosnia, Macedonia: 30 дней

### Asia (популярные для BY туристов)
- Turkey: 30 дней
- Georgia: 365 дней (король visa-free!)
- UAE, Jordan, Oman, Qatar, Bahrain: 30 дней

### North Africa
- Tunisia, Morocco: 30 дней
- Egypt: visa on arrival

### Asia-Pacific
- Vietnam, Thailand, Indonesia: 30 дней
- Sri Lanka, Maldives: 30 дней

### Americas
- Cuba, Mexico, Argentina, Brazil, Costa Rica, Panama: 30 дней

## Реальная экономика

### Before (наивный подход)
```
Сценарий: n8n запускается каждые 6 часов, проверяет 20 стран
- 20 вызовов × $0.02 = $0.40 за запуск
- 4 запуска/день × $0.40 = $1.60/день
- 30 дней × $1.60 = $48/месяц
```

### After (гибридный подход)
```
Сценарий: то же самое
- 20 стран: 18 из KNOWN_VISA_FREE_BY (0 вызовов) + 2 unknown (2 вызова)
- 2 вызова × $0.02 = $0.04 за запуск
- 4 запуска/день × $0.04 = $0.16/день
- 30 дней × $0.16 = $4.80/месяц
- **Экономия: 90%!**
```

---

## Как это работает в коде

### src/visa/checker.py

```python
async def _check_country(self, country_code: str, country_name: str) -> VisaRequirement:
    """Hybrid visa check: DB cache → local table → web search."""
    
    # Step 1: Check DB cache (fresh within 24h)
    cached = await self._repo.get_visa(country_code, self._passport)
    if cached and not cached.is_expired:
        logger.info("visa.cache_hit_db")
        return cached  # ← 0 cost
    
    # Step 2: Check local KNOWN_VISA_FREE_BY table
    if country_code in KNOWN_VISA_FREE_BY:
        # Create requirement from local data
        requirement = VisaRequirement(...)
        await self._repo.save_visa(requirement)  # Save for future
        logger.info("visa.cache_hit_local")
        return requirement  # ← 0 cost
    
    # Step 3: Web search ONLY for unknown countries
    if not self._brave_key:
        logger.warning("visa.no_api_key", action="returning UNKNOWN")
        return VisaRequirement(visa_status=UNKNOWN)  # ← 0 cost
    
    # Only reaches here for unknown country + brave_key available
    requirement = await self._search_visa_info(country_code, country_name)
    await self._repo.save_visa(requirement)
    logger.info("visa.web_search")
    return requirement  # ← $0.01-0.05 cost
```

---

## Конфигурация

### .env.example

```env
# OPTIONAL - if not set, uses local KNOWN_VISA_FREE_BY table
BRAVE_SEARCH_API_KEY=
```

### Поведение

| Сценарий | BRAVE_SEARCH_API_KEY | Результат |
|----------|----------------------|-----------|
| Known country (Turkey) | any | ✅ Из локальной таблицы (0 cost) |
| Unknown country | SET | ✅ Brave Search (real-time) |
| Unknown country | EMPTY | ⚠️ UNKNOWN status (safe default) |

---

## Когда добавлять новые страны в KNOWN_VISA_FREE_BY

**Правило:** Добавлять только СТАБИЛЬНЫЕ визовые режимы:
- ✅ Официальные соглашения (CIS, Balkans)
- ✅ Известные visa-free (Turkey 30 дней, Georgia 365)
- ✅ Visa on arrival (Egypt)
- ❌ Нестабильные визовые режимы (могут меняться)
- ❌ Требующие доп. документов (медицинские справки, etc.)

**Когда проверять:**
- Каждый квартал (~4 раза в год)
- Если новость о закрытии границ → проверить немедленно

---

## Phase 2+: LLM-as-Judge для неизвестных

Когда бюджет позволит:

```python
# Для UNKNOWN-статусов можно добавить cheap LLM check
if visa_status == VisaStatus.UNKNOWN and brave_results_ambiguous:
    # Используем Haiku ($0.001/запрос) для уточнения
    judge_response = await claude_haiku.check_visa_ambiguity(
        country=country_name,
        search_results=results,
    )
    visa_status = parse_judge_response(judge_response)
```

**Стоимость:** $0.001 × 2-3 unknown/день = $3/месяц (допустимо)

---

## Мониторинг гибридного режима

API endpoint `/ops/stats` теперь показывает:

```json
{
  "visa_cache": {
    "active_entries": 47,
    "from_db": 32,
    "from_local_table": 15,
    "api_calls_today": 2
  }
}
```

**Интерпретация:**
- `from_db`: 32 - кеш БД работает (экономим 32 Brave запроса!)
- `from_local_table`: 15 - локальная таблица помогает
- `api_calls_today`: 2 - только для реально неизвестных стран

---

## Заключение

**Гибридный подход = Production-grade решение:**
- ✅ 90% экономия на API costs
- ✅ Нет зависимости от Brave Search (может быть DOWN)
- ✅ Instant response для 30+ популярных стран
- ✅ Fallback на UNKNOWN для редких случаев
- ✅ Масштабируется без дополнительных расходов

**Готово для запуска с нулевым бюджетом на visa checks!**
