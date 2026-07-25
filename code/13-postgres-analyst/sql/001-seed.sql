-- Seed data for posts 13 and 14.
--
-- Three tables with real foreign keys between them, so that
-- postgres://relationships has something to say and the model has something to
-- join. Small enough to read, large enough that a missing LIMIT would matter.
--
-- Run automatically by docker-compose on first start. To load it by hand:
--   psql -U postgres -d analytics -f sql/001-seed.sql

BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT        NOT NULL UNIQUE,
    full_name   TEXT        NOT NULL,
    country     TEXT        NOT NULL,
    active      BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id          BIGSERIAL PRIMARY KEY,
    sku         TEXT           NOT NULL UNIQUE,
    name        TEXT           NOT NULL,
    category    TEXT           NOT NULL,
    price       NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    in_stock    INTEGER        NOT NULL DEFAULT 0 CHECK (in_stock >= 0)
);

CREATE TABLE IF NOT EXISTS orders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT         NOT NULL REFERENCES users (id),
    product_id  BIGINT         NOT NULL REFERENCES products (id),
    quantity    INTEGER        NOT NULL CHECK (quantity > 0),
    total       NUMERIC(10, 2) NOT NULL CHECK (total >= 0),
    status      TEXT           NOT NULL DEFAULT 'pending',
    placed_at   TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS orders_user_id_idx ON orders (user_id);
CREATE INDEX IF NOT EXISTS orders_placed_at_idx ON orders (placed_at DESC);

INSERT INTO users (email, full_name, country, active) VALUES
    ('ada@example.com',      'Ada Lovelace',      'GB', true),
    ('grace@example.com',    'Grace Hopper',      'US', true),
    ('alan@example.com',     'Alan Turing',       'GB', false),
    ('katherine@example.com','Katherine Johnson', 'US', true),
    ('tim@example.com',      'Tim Berners-Lee',   'GB', true),
    ('radia@example.com',    'Radia Perlman',     'US', true),
    ('barbara@example.com',  'Barbara Liskov',    'US', true),
    ('margaret@example.com', 'Margaret Hamilton', 'US', false)
ON CONFLICT (email) DO NOTHING;

INSERT INTO products (sku, name, category, price, in_stock) VALUES
    ('KB-001', 'Mechanical keyboard',   'peripherals', 129.00,  42),
    ('MS-002', 'Trackball mouse',       'peripherals',  79.50,  17),
    ('MN-003', '27 inch monitor',       'displays',    319.99,   8),
    ('MN-004', '34 inch ultrawide',     'displays',    629.00,   3),
    ('DK-005', 'Standing desk',         'furniture',   549.00,  11),
    ('CH-006', 'Ergonomic chair',       'furniture',   389.00,   0),
    ('HP-007', 'Noise cancelling head', 'audio',       249.00,  23),
    ('CB-008', 'USB-C dock',            'peripherals', 159.00,  31)
ON CONFLICT (sku) DO NOTHING;

INSERT INTO orders (user_id, product_id, quantity, total, status, placed_at) VALUES
    (1, 1, 1, 129.00, 'shipped',   now() - INTERVAL '30 days'),
    (1, 3, 2, 639.98, 'shipped',   now() - INTERVAL '28 days'),
    (2, 5, 1, 549.00, 'delivered', now() - INTERVAL '21 days'),
    (2, 2, 1,  79.50, 'delivered', now() - INTERVAL '20 days'),
    (3, 7, 1, 249.00, 'cancelled', now() - INTERVAL '18 days'),
    (4, 4, 1, 629.00, 'shipped',   now() - INTERVAL '14 days'),
    (4, 8, 2, 318.00, 'pending',   now() - INTERVAL '9 days'),
    (5, 1, 3, 387.00, 'pending',   now() - INTERVAL '7 days'),
    (6, 6, 1, 389.00, 'pending',   now() - INTERVAL '5 days'),
    (7, 3, 1, 319.99, 'shipped',   now() - INTERVAL '3 days'),
    (7, 2, 2, 159.00, 'pending',   now() - INTERVAL '2 days'),
    (8, 8, 1, 159.00, 'pending',   now() - INTERVAL '1 day');

COMMIT;
