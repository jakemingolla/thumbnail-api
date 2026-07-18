terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state only — v1 targets LocalStack, not a remote backend.
  backend "local" {
    path = "terraform.tfstate"
  }
}
