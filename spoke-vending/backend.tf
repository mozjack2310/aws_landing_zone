terraform {
  backend "s3" {
    key          = "spoke-vending/forrad-dev/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
