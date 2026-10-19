# Vault independiente en AWS Learner Lab

Este stack gestiona exclusivamente `aws_instance.vault` y
`aws_security_group.vault`. Reutiliza VPC, subnet, perfil IAM y bucket de estado;
no es propietario de EKS. La AMI se resuelve mediante el parámetro público de
Amazon Linux 2023 x86_64. Los cambios de AMI no reemplazan automáticamente un
Vault existente; actualizar el sistema requiere una operación planificada.

Copiar `terraform.tfvars.example` a `terraform.tfvars`, completar los identificadores
y el CIDR administrativo, y seguir [el procedimiento completo](../../../docs/vault-lab.md).
El estado usa `modulo-vault/terraform.tfstate`, independiente de EKS y DynamoDB.

El bucket de transferencias SSM se prepara con `scripts/vault/aws_prepare.py`.
Se evita gestionarlo mediante un recurso S3 de Terraform por la restricción
`GetBucketObjectLockConfiguration` documentada para este Learner Lab.

`terraform destroy` elimina esta EC2 y sus datos locales. Guardar un snapshot
Raft y las participaciones de unseal fuera de la instancia antes de destruir.
