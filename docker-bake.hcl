// Reuse both existing builds from the same checkout; no published base required.
target "api" {
  context = "backend"
  args = { PRINTSTASH_VARIANT = "full" }
}

target "frontend" {
  context = "frontend"
}

target "unified" {
  context = "backend/unified"
  contexts = {
    api-image = "target:api"
    frontend-image = "target:frontend"
  }
  tags = ["printstash:local"]
}
