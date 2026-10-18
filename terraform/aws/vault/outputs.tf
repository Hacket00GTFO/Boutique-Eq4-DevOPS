output "instance_id" {
  value = aws_instance.vault.id
}

output "private_ip" {
  value = aws_instance.vault.private_ip
}

output "public_ip" {
  value = aws_instance.vault.public_ip
}

output "security_group_id" {
  value = aws_security_group.vault.id
}
