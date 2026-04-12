-- Travel Sentinel - PostgreSQL Schema
-- Run: psql -U sentinel -d travel_sentinel -f sql/001_init.sql

-- =====================================================
-- ENUM TYPES
-- =====================================================

CREATE TYPE deal_type AS ENUM ('flight', 'tour', 'cruise');
CREATE TYPE visa_status AS ENUM ('visa_free', 'visa_on_arrival', 'e_visa', 'visa_required', 'unknown');
CREATE TYPE alert_status AS ENUM ('new', 'sent', 'expired', 'dismissed');

-- =====================================================
-- DEALS - все найденные предложения
-- =====================================================

CREATE TABLE IF NOT EXISTS deals (
    id              BIGSERIAL PRIMARY KEY,
    deal_type       deal_type NOT NULL,
    source          VARCHAR(100) NOT NULL,          -- 'aviasales', 'travelata', 'dreamlines'
    destination     VARCHAR(200) NOT NULL,           -- 'Turkey, Antalya'
    country_code    CHAR(2) NOT NULL,                -- ISO 3166-1 alpha-2: TR, EG, TH...
    departure_city  VARCHAR(100) NOT NULL,           -- 'Minsk', 'Moscow'
    departure_code  VARCHAR(5) NOT NULL,             -- MSQ, SVO, LED
    departure_date  DATE NOT NULL,
    return_date     DATE,
    nights          SMALLINT,
    price_eur       NUMERIC(10, 2) NOT NULL,
    price_original  NUMERIC(10, 2),                  -- цена в оригинальной валюте
    currency        CHAR(3) DEFAULT 'EUR',           -- оригинальная валюта
    hotel_name      VARCHAR(300),
    hotel_stars     SMALLINT,
    meal_plan       VARCHAR(50),                     -- 'AI', 'HB', 'BB', 'RO'
    url             TEXT NOT NULL,
    checksum        VARCHAR(64) NOT NULL UNIQUE,     -- SHA256 от ключевых полей (дедупликация)
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_deals_destination ON deals (country_code, departure_date);
CREATE INDEX idx_deals_price ON deals (price_eur);
CREATE INDEX idx_deals_scraped ON deals (scraped_at);
CREATE INDEX idx_deals_checksum ON deals (checksum);

-- =====================================================
-- PRICE HISTORY - для отслеживания динамики цен
-- =====================================================

CREATE TABLE IF NOT EXISTS price_history (
    id          BIGSERIAL PRIMARY KEY,
    deal_id     BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    price_eur   NUMERIC(10, 2) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_price_history_deal ON price_history (deal_id, recorded_at);

-- =====================================================
-- VISA REQUIREMENTS - кеш визовых данных
-- =====================================================

CREATE TABLE IF NOT EXISTS visa_requirements (
    id              BIGSERIAL PRIMARY KEY,
    country_code    CHAR(2) NOT NULL,
    country_name    VARCHAR(200) NOT NULL,
    passport_type   VARCHAR(20) NOT NULL DEFAULT 'BY', -- BY = белорусский, RU = российский
    visa_status     visa_status NOT NULL DEFAULT 'unknown',
    max_stay_days   SMALLINT,                           -- безвиз: сколько дней разрешено
    notes           TEXT,                               -- доп. условия
    source_url      TEXT,                               -- откуда взята информация
    verified_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- когда проверено
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (country_code, passport_type)
);

CREATE INDEX idx_visa_country ON visa_requirements (country_code, passport_type);
CREATE INDEX idx_visa_expires ON visa_requirements (expires_at);

-- =====================================================
-- TRANSIT VISA REQUIREMENTS - транзитные визы
-- =====================================================

CREATE TABLE IF NOT EXISTS transit_requirements (
    id              BIGSERIAL PRIMARY KEY,
    transit_country CHAR(2) NOT NULL,                  -- страна транзита
    passport_type   VARCHAR(20) NOT NULL DEFAULT 'BY',
    visa_required   BOOLEAN NOT NULL DEFAULT TRUE,
    max_transit_hrs SMALLINT,                          -- макс. часов без визы
    notes           TEXT,
    verified_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    UNIQUE (transit_country, passport_type)
);

-- =====================================================
-- ALERTS - отправленные уведомления (дедупликация)
-- =====================================================

CREATE TABLE IF NOT EXISTS alerts (
    id          BIGSERIAL PRIMARY KEY,
    deal_id     BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    status      alert_status NOT NULL DEFAULT 'new',
    message     TEXT NOT NULL,
    sent_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_alerts_deal ON alerts (deal_id, status);

-- =====================================================
-- SCRAPE LOG - лог запусков скрейперов
-- =====================================================

CREATE TABLE IF NOT EXISTS scrape_log (
    id              BIGSERIAL PRIMARY KEY,
    scraper_name    VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL,               -- 'success', 'error', 'partial'
    deals_found     INT DEFAULT 0,
    deals_new       INT DEFAULT 0,
    error_message   TEXT,
    duration_ms     INT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ
);
