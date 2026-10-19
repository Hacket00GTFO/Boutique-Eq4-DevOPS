# Exact KV v2 data endpoint only; no list, metadata or management access.
path "secret/data/boutique/lab/integration" {
  capabilities = ["read"]
}
