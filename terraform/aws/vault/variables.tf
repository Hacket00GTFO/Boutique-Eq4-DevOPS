variable "region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_id" {
  type        = string
  description = "Existing EKS VPC; this stack never owns or destroys it."
}

variable "subnet_id" {
  type        = string
  description = "Public subnet in that VPC, with outbound HTTPS for SSM."
}

variable "instance_profile" {
  type        = string
  default     = "LabInstanceProfile"
  description = "Existing profile with SSM permissions; no IAM roles are created."
}

variable "admin_cidrs" {
  type        = set(string)
  description = "Explicit IPv4 networks allowed to use the HTTPS UI on 8200. Use /32 for operators."
  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for c in var.admin_cidrs : can(cidrnetmask(c)) && try(tonumber(split("/", c)[1]) >= 24, false)])
    error_message = "Provide explicit IPv4 admin CIDRs of /24 or narrower; public-wide ingress is forbidden."
  }
}

variable "collector_cidrs" {
  type        = set(string)
  default     = []
  description = "EKS subnet CIDRs allowed to read Vault health for the in-cluster operations collector. Keep each entry /24 or narrower."
  validation {
    condition     = alltrue([for c in var.collector_cidrs : can(cidrnetmask(c)) && try(tonumber(split("/", c)[1]) >= 24, false)])
    error_message = "Provide explicit IPv4 collector CIDRs of /24 or narrower."
  }
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}
