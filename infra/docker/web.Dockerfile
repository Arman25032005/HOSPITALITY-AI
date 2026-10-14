# Next.js production build. Build context: repository root.
FROM node:22-alpine AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
# Behind a TLS-inspecting proxy, supply its CA:  docker build --secret id=ca,src=ca-bundle.crt ...
RUN --mount=type=secret,id=ca,target=/run/secrets/ca.crt,required=false \
    set -eu; \
    if [ -s /run/secrets/ca.crt ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca.crt; fi; \
    npm ci
COPY apps/web .
RUN --mount=type=secret,id=ca,target=/run/secrets/ca.crt,required=false \
    set -eu; \
    if [ -s /run/secrets/ca.crt ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca.crt; fi; \
    npm run build && npm prune --omit=dev

FROM node:22-alpine
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
RUN addgroup -S -g 10001 hospitality && adduser -S -u 10001 -G hospitality hospitality
COPY --from=build --chown=hospitality:hospitality /app/package.json ./package.json
COPY --from=build --chown=hospitality:hospitality /app/node_modules ./node_modules
COPY --from=build --chown=hospitality:hospitality /app/.next ./.next
COPY --from=build --chown=hospitality:hospitality /app/next.config.js ./next.config.js
USER hospitality
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD wget -qO- http://localhost:3000/login >/dev/null || exit 1
CMD ["npx", "next", "start", "-p", "3000"]
