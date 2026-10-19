CREATE TABLE boutique.operations_reports (
 kind text NOT NULL CHECK(kind IN ('vault','ansible')),
 run_id uuid NOT NULL,
 sequence bigint NOT NULL CHECK(sequence > 0),
 payload jsonb NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(kind,run_id)
);
CREATE INDEX operations_reports_recent ON boutique.operations_reports(kind,received_at DESC);
