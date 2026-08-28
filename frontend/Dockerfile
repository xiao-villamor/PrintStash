FROM node:24-alpine AS base
RUN corepack enable && corepack prepare pnpm@latest-10 --activate

FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/ui/package.json ./packages/ui/package.json
COPY packages/domain/package.json ./packages/domain/package.json
RUN pnpm install --no-frozen-lockfile --prod=false

FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/packages/ui/node_modules ./packages/ui/node_modules
COPY . .

# Client SPA build → static dist/. The API is reached same-origin via the
# nginx /api/v1 proxy, so no API URL needs to be baked in at build time.
RUN pnpm run build

FROM nginxinc/nginx-unprivileged:alpine AS runner
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY security-headers.conf /etc/nginx/security-headers.conf

# The whole-*request* ceiling, not the per-file upload cap: 512 MiB of file plus
# the 16 MiB of multipart headroom the backend allows (`MULTIPART_OVERHEAD_BYTES`).
# Set to the per-file number, nginx rejects a file *at* the documented limit with
# an HTML 413 the SPA cannot read, before the API can say `upload_too_large`.
ENV NGINX_CLIENT_MAX_BODY_SIZE=528m

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
