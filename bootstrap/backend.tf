terraform {
  backend "s3" {
    bucket       = "corp-platform-tfstate-052535487590-us-east-1"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
