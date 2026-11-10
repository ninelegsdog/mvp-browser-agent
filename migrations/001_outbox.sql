-- Таблица транзакционного outbox (идемпотентная доставка событий)
CREATE TABLE IF NOT EXISTS outbox (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_unprocessed
    ON outbox (processed_at, id)
    WHERE processed_at IS NULL;
