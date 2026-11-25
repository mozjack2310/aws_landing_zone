terraform {
  backend "s3" {
    key          = "governance/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
