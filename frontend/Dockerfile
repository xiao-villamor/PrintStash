FROM node:24-alpine AS base
RUN corepack enable && corepack prepare pnpm@latest-10 --activate

FROM base AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --no-frozen-lockfile --prod=false

FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Client SPA build → static dist/. The API is reached same-origin via the
# nginx /api/v1 proxy, so no API URL needs to be baked in at build time.
RUN pnpm run build

FROM nginx:alpine AS runner
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template

ENV NGINX_CLIENT_MAX_BODY_SIZE=512m

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
