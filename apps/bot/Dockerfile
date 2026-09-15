# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Telegram bot image. Long-polls Telegram (no inbound port), so this needs
# no reverse-proxy route — only outbound HTTPS to Telegram and Anthropic.
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* .npmrc* ./
COPY apps/bot/package.json ./apps/bot/
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --filter @med/bot... || \
    pnpm install --filter @med/bot...

FROM deps AS build
COPY apps/bot ./apps/bot
RUN pnpm --filter @med/bot build

FROM base AS runtime
ENV NODE_ENV=production
# Runs unprivileged. The node image already provides uid/gid 1000.
RUN mkdir -p /data && chown -R node:node /data
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/apps/bot/node_modules ./apps/bot/node_modules
COPY --from=build --chown=node:node /app/apps/bot/dist ./apps/bot/dist
COPY --chown=node:node apps/bot/package.json ./apps/bot/
# The bot's only knowledge of the project — read at start-up, not baked into
# logic, so updating a doc and redeploying is enough to refresh it.
COPY --chown=node:node docs ./docs

USER node
WORKDIR /app/apps/bot

CMD ["node", "dist/server.js"]
