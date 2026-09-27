// Reuse both existing builds from the same checkout; no published base required.
target "api" {
  context = "backend"
  args = { PRINTSTASH_VARIANT = "full" }
}

target "api-lite" {
  context = "backend"
  args = { PRINTSTASH_VARIANT = "lite" }
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

group "publish" {
  targets = ["api", "api-lite", "frontend", "unified"]
}

group "default" {
  targets = ["unified"]
}
