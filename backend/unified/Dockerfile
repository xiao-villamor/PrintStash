# syntax=docker/dockerfile:1
FROM api-image

RUN apt-get update \
 && apt-get install -y --no-install-recommends nginx-light gettext-base tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=frontend-image /usr/share/nginx/html /usr/share/nginx/html
COPY --from=frontend-image /etc/nginx/templates/default.conf.template /app/nginx-server.template
COPY --from=frontend-image /etc/nginx/security-headers.conf /etc/nginx/security-headers.conf
# Retain the shared SPA, caching, security-header and WebSocket rules.
RUN sed -i 's|http://api:8000|http://127.0.0.1:8000|' /app/nginx-server.template
COPY --chmod=0644 nginx.conf /app/nginx.conf
COPY --chmod=0755 run.sh /app/unified-run.sh

# Source files can arrive from a checkout with umask 077. The API's COPY owns
# them as UID 10001; arbitrary PUID/PGID must also be able to read that code.
RUN chmod -R a+rX /app/app /app/alembic

ENV NGINX_CLIENT_MAX_BODY_SIZE=528m
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS http://127.0.0.1:3000/api/v1/health && curl -fsS -o /dev/null http://127.0.0.1:3000/
# Inherit the API entrypoint: repair volume ownership, drop privileges, migrate.
CMD ["/usr/bin/tini", "--", "/app/unified-run.sh"]
