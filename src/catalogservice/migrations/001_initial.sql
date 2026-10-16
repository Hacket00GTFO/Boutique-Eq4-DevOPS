CREATE SCHEMA IF NOT EXISTS boutique;
CREATE TABLE boutique.products (
 id text PRIMARY KEY, name text NOT NULL, description text NOT NULL DEFAULT '',
 picture text NOT NULL DEFAULT '', categories text[] NOT NULL DEFAULT '{}',
 price_units bigint NOT NULL CHECK(price_units >= 0),
 price_nanos integer NOT NULL DEFAULT 0 CHECK(price_nanos BETWEEN 0 AND 999999999),
 active boolean NOT NULL DEFAULT true, sku text NOT NULL UNIQUE,
 variant text NOT NULL DEFAULT 'Standard', warehouse text NOT NULL DEFAULT 'Principal',
 stock integer NOT NULL DEFAULT 0 CHECK(stock >= 0),
 reserved integer NOT NULL DEFAULT 0 CHECK(reserved >= 0 AND reserved <= stock),
 version integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE boutique.admin_users (
 id uuid PRIMARY KEY, email text NOT NULL UNIQUE, password_hash text NOT NULL,
 role text NOT NULL CHECK(role IN ('admin','viewer')), active boolean NOT NULL DEFAULT true
);
CREATE TABLE boutique.sessions (
 token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES boutique.admin_users,
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE boutique.login_attempts (
 email text PRIMARY KEY, failures integer NOT NULL DEFAULT 0,
 window_start timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE boutique.orders (
 id uuid PRIMARY KEY, user_id text NOT NULL, idempotency_key text NOT NULL,
 email text NOT NULL, currency text NOT NULL, total_nanos numeric(30,0) NOT NULL,
 total_usd_nanos numeric(30,0) NOT NULL,
 state text NOT NULL CHECK(state IN ('reserved','charging','payment_unknown','paid','confirmed','failed','cancelled')),
 fulfillment text NOT NULL DEFAULT 'PENDIENTE', payload jsonb NOT NULL,
 transaction_id text NOT NULL DEFAULT '', payment_method text NOT NULL,
 email_sent boolean NOT NULL DEFAULT false, email_lease_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,idempotency_key)
);
CREATE TABLE boutique.order_items (
 order_id uuid NOT NULL REFERENCES boutique.orders, product_id text NOT NULL REFERENCES boutique.products,
 name text NOT NULL, sku text NOT NULL, quantity integer NOT NULL CHECK(quantity > 0),
 unit_nanos numeric(30,0) NOT NULL CHECK(unit_nanos >= 0), PRIMARY KEY(order_id,product_id)
);
CREATE TABLE boutique.stock_movements (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, product_id text NOT NULL REFERENCES boutique.products,
 delta integer NOT NULL, reason text NOT NULL, actor text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE boutique.audit_log (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, actor text NOT NULL,
 action text NOT NULL, entity text NOT NULL, request_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX orders_created ON boutique.orders(created_at DESC,id);
CREATE INDEX orders_state ON boutique.orders(state,created_at);
CREATE INDEX sessions_expiry ON boutique.sessions(expires_at);
CREATE INDEX products_category ON boutique.products USING gin(categories);
