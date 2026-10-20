# Configuración de Vault por Ansible/SSM

Controlador Ubuntu 26.04 x86_64 nativo o WSL, Python 3.14, AWS CLI y plugin Session Manager.
Instalar `requirements.txt` en un entorno virtual (`python3 -m venv .venv`)
y las colecciones de `requirements.yml`.
Usar el inventario dinámico predeterminado; filtra la instancia
`Name=boutique-vault-lab` en `us-east-1`.

```bash
cd ansible
export ANSIBLE_SSM_BUCKET=boutique-vault-ssm-NOMBRE-UNICO
ansible-galaxy collection install -r requirements.yml
ansible-playbook site.yml
```

Los roles instalan dependencias, Vault verificado por SHA256, TLS, configuración
persistente y systemd. No inicializan, no desprecintan ni cargan credenciales.
El certificado público se exporta a `.vault-lab/certs/<instance-id>.pem`;
la clave privada no abandona EC2. No usar `--diff` ni verbosidad de depuración
durante operaciones que procesen material privado.

Los roles no fuerzan renovaciones TLS: repetir debe producir `changed=0`.
Para una renovación planificada: `ansible-playbook site.yml -e vault_tls_renew=true`,
actualizar la confianza de CI/navegador y ejecutar unseal si el servicio reinició.
Después de un cambio de IP pública, volver a ejecutar Ansible para actualizar SANs.

Configuración, bootstrap, recuperación y evidencias: [guía de Fase 3](../docs/vault-lab.md).
