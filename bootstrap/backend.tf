terraform {
  backend "s3" {
    bucket       = "corp-platform-tfstate-<AWS_ACCOUNT_ID>-us-east-1"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
