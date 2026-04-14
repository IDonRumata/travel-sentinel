# Deployment Guide - Production VPS

Развёртывание Travel Sentinel на реальный сервер.

## Требования

- VPS с Ubuntu 22.04 LTS (или CentOS, Debian)
- 2GB RAM минимум (1GB для DB + 512MB для API + остальное система)
- 30GB диск (для истории цен)
- Статический IP (для белого листа API)
- SSH доступ

**Рекомендуемый провайдер:** DigitalOcean, Vultr, Hetzner (дешёвые, надёжные для BY/RU)

---

## Шаг 1: Базовая подготовка VPS

```bash
ssh root@your.server.ip

# Обновить систему
apt update && apt upgrade -y

# Установить необходимое
apt install -y curl wget git htop

# Установить Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Добавить текущего пользователя в docker группу
usermod -aG docker $USER
newgrp docker

# Проверить
docker --version
docker compose --version
```

---

## Шаг 2: Клонирование и подготовка

```bash
cd /opt
git clone https://github.com/IDonRumata/travel-sentinel.git
cd travel-sentinel

# Создать .env для production
nano .env
```

Заполнить `.env` с production значениями:

```env
# PostgreSQL - увеличить ресурсы
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=travel_sentinel
POSTGRES_USER=sentinel
POSTGRES_PASSWORD=CHANGE_ME_VERY_STRONG_PASSWORD

# API ключи (обязательно!)
ANTHROPIC_API_KEY=sk-ant-...
BRAVE_SEARCH_API_KEY=BSA-...
AVIASALES_TOKEN=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Production настройки
LOG_LEVEL=WARNING  # только важное в логи
SCRAPE_INTERVAL_MINUTES=60
MAX_PRICE_PER_PERSON_USD=400
```

**ВАЖНО:** сохранить пароль в безопасном месте (KeePass, etc.).

---

## Шаг 3: Запуск через Docker Compose

```bash
# Проверить compose файл
docker compose config

# Запустить в background
docker compose up -d

# Проверить что всё запустилось
docker compose ps
# Должны быть: db (healthy) и api (running)

# Смотреть логи
docker compose logs -f api
docker compose logs -f db
```

---

## Шаг 4: Nginx Reverse Proxy (опционально, но рекомендуется)

Nginx защитит API от прямого доступа и позволит использовать SSL.

### 4.1 Установка Nginx

```bash
apt install -y nginx certbot python3-certbot-nginx

# Запустить
systemctl start nginx
systemctl enable nginx
```

### 4.2 Конфигурация

```bash
nano /etc/nginx/sites-available/travel-sentinel
```

```nginx
server {
    listen 80;
    server_name api.yourdom.com;  # или IP

    location / {
        proxy_pass http://localhost:8100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts для длинных скрейпов
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }

    # Rate limiting для защиты от DDoS
    location /tools/search-deals {
        limit_req zone=api burst=5 nodelay;
        proxy_pass http://localhost:8100;
    }
}

# Определить зону rate limit
limit_req_zone $binary_remote_addr zone=api:10m rate=5r/m;
```

```bash
# Включить конфиг
ln -s /etc/nginx/sites-available/travel-sentinel /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 4.3 SSL (Let's Encrypt)

```bash
certbot --nginx -d api.yourdom.com
# или IP если нет домена - просто оставить HTTP
```

---

## Шаг 5: n8n на VPS

### Вариант A: Docker (рекомендуется)

```bash
docker run -d \
  --name n8n \
  -p 5679:5678 \
  -v n8n_data:/home/node/.n8n \
  -e NODE_ENV=production \
  -e GENERIC_TIMEZONE="UTC" \
  n8nio/n8n
```

Откроется на http://your.server.ip:5679

### Вариант B: npm глобально

```bash
apt install -y nodejs npm
npm install -g n8n
n8n start &
```

### Шаг 5.2: Импорт workflow в n8n

1. Открыть n8n в браузере
2. Menu → Import from file
3. Выбрать `n8n/travel_sentinel_workflow.json`
4. Добавить credentials (Anthropic API, Telegram)
5. Активировать workflow (переключатель Activate)

---

## Шаг 6: Мониторинг и Логирование

### 6.1 Настроить логирование

```bash
# Создать папку для логов
mkdir -p /var/log/travel-sentinel

# Перенаправить docker логи
docker compose logs -f > /var/log/travel-sentinel/api.log &
```

### 6.2 Health check в systemd

Создать скрипт мониторинга:

```bash
cat > /opt/travel-sentinel/health-check.sh << 'EOF'
#!/bin/bash
RESPONSE=$(curl -s http://localhost:8100/health)
STATUS=$(echo $RESPONSE | jq -r '.status')

if [ "$STATUS" != "ok" ] && [ "$STATUS" != "partial" ]; then
    # API упал - отправить алерт
    curl -X POST https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage \
      -d chat_id=$TELEGRAM_CHAT_ID \
      -d text="🔴 Travel Sentinel API DOWN! Status: $STATUS"
    exit 1
fi
exit 0
EOF

chmod +x /opt/travel-sentinel/health-check.sh
```

Добавить в crontab:

```bash
crontab -e
```

```cron
# Проверять каждые 5 минут
*/5 * * * * /opt/travel-sentinel/health-check.sh
```

---

## Шаг 7: Backup БД

```bash
# Ручной бэкап
docker compose exec db pg_dump -U sentinel travel_sentinel > backup.sql

# Автоматический бэкап (каждый день в 3 ночи)
cat > /opt/travel-sentinel/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/travel-sentinel/backups"
mkdir -p $BACKUP_DIR
FILENAME="$BACKUP_DIR/backup-$(date +%Y%m%d-%H%M%S).sql"

docker compose -f /opt/travel-sentinel/docker-compose.yml \
  exec -T db pg_dump -U sentinel travel_sentinel > $FILENAME

# Удалить бэкапы старше 30 дней
find $BACKUP_DIR -type f -mtime +30 -delete

echo "Backup created: $FILENAME"
EOF

chmod +x /opt/travel-sentinel/backup.sh
```

Добавить в crontab:

```cron
# Каждый день в 3 ночи
0 3 * * * /opt/travel-sentinel/backup.sh
```

---

## Шаг 8: Обновление кода

```bash
cd /opt/travel-sentinel

# Получить новые изменения
git pull origin main

# Пересоздать контейнеры
docker compose up -d --build

# Проверить логи
docker compose logs -f api
```

---

## Troubleshooting Production

| Проблема | Решение |
|----------|---------|
| `docker: command not found` | Переустановить Docker: `curl -fsSL https://get.docker.com \| sh` |
| API медленно отвечает | Увеличить RAM VM или оптимизировать БД: `ANALYZE; VACUUM;` |
| Telegram не получает сообщения | Проверить `TELEGRAM_CHAT_ID` (число) и интернет на VPS |
| Тravelata/Aviasales возвращают 403 | Circuit Breaker ловит - смотреть `/ops/stats`. Может понадобиться прокси. |
| `Permission denied` при git pull | `sudo chown -R $USER /opt/travel-sentinel` |

---

## Мониторинг через n8n Dashboard

На рабочем столе n8n создать дашборд:

1. New Workflow → HTTP Request → GET `http://api:8100/ops/stats`
2. Scheduled trigger (раз в час)
3. Parse JSON → Extract: deals_found, scrapers.circuits
4. Send to Telegram

Пример:
```
Сегодня собрано туров: 1240
Новых предложений: 15
Circuit Breaker status: ОК
Visa cache: 42 записей
```

---

## Production Checklist

- ✅ VPS с 2GB RAM
- ✅ Docker + Docker Compose установлены
- ✅ `.env` заполнен (все ключи)
- ✅ `docker compose up -d` работает
- ✅ `/health` возвращает `ok`
- ✅ n8n импортирован и активирован
- ✅ Telegram credentials добавлены
- ✅ SSL сертификат (если доменное имя)
- ✅ Health check скрипт в crontab
- ✅ Бэкапы настроены
- ✅ Логирование работает

Готово к production! 🚀
