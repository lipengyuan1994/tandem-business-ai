terraform {
  required_version = ">= 1.6"

  required_providers {
    vultr = {
      source  = "vultr/vultr"
      version = "= 2.33.2" # pinned; arguments checked against `terraform providers schema` on 2026-10-25
    }
  }
}
