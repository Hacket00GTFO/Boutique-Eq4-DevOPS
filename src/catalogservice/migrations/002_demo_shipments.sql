ALTER TABLE boutique.orders
 ADD COLUMN demo boolean NOT NULL DEFAULT false,
 ADD COLUMN customer_name text NOT NULL DEFAULT '',
 ADD COLUMN shipping_carrier text NOT NULL DEFAULT '',
 ADD COLUMN shipping_notes text NOT NULL DEFAULT '',
 ADD COLUMN estimated_delivery date,
 ADD COLUMN shipment_version integer NOT NULL DEFAULT 1;

CREATE TABLE boutique.shipment_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 order_id uuid NOT NULL REFERENCES boutique.orders,
 status text NOT NULL, carrier text NOT NULL DEFAULT '',
 tracking_id text NOT NULL DEFAULT '', notes text NOT NULL DEFAULT '',
 actor text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX shipment_events_order ON boutique.shipment_events(order_id, id DESC);
