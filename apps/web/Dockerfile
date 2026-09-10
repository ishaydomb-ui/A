# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Web image: builds the client, then serves the static bundle from nginx.
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS build
ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
WORKDIR /app

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* .npmrc* ./
COPY packages/shared/package.json ./packages/shared/
COPY apps/web/package.json ./apps/web/
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --filter @med/web... || \
    pnpm install --filter @med/web...

COPY packages/shared ./packages/shared
COPY apps/web ./apps/web
RUN pnpm --filter @med/shared build && pnpm --filter @med/web build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
