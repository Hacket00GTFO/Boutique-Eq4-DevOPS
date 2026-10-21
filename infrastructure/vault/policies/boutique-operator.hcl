# Named lab operator, separate from root and CI. Includes audit, policies,
# bootstrap/rotation and recovery; use only in a controlled operator session.
path "secret/*" {
  capabilities = ["create", "read", "update", "delete", "list", "patch"]
}
path "sys/mounts*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "sys/audit*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "sys/policies/acl/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "auth/token/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "sys/capabilities-self" {
  capabilities = ["update"]
}
path "sys/seal" {
  capabilities = ["update", "sudo"]
}
# Vault 2.x authenticates these recovery APIs even when the caller holds a quorum.
path "sys/generate-root/attempt" {
  capabilities = ["read", "update", "delete", "sudo"]
}
path "sys/generate-root/update" {
  capabilities = ["update", "sudo"]
}
path "sys/rekey/*" {
  capabilities = ["read", "update", "delete", "sudo"]
}
path "sys/auth" {
  capabilities = ["read"]
}
path "sys/auth/github" {
  capabilities = ["create", "update", "sudo"]
}
path "auth/github/config" {
  capabilities = ["create", "read", "update"]
}
path "auth/github/role/boutique-main" {
  capabilities = ["create", "read", "update"]
}
path "sys/storage/raft/snapshot" {
  capabilities = ["read"]
}
