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
