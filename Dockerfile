# Beitenu production image.
#
# Two stages so the shipped image carries no compilers and no dev dependencies.
# better-sqlite3 is a native module, so the build stage needs a toolchain — but
# Next's standalone output already contains the compiled binding, so the runtime
# stage needs neither the toolchain nor a node_modules install of its own.

# ---------------------------------------------------------------- build
FROM node:22-slim AS build
WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

# Build tools for better-sqlite3's native binding. Discarded with this stage.
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 make g++ ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# The build imports src/lib/db.ts, which opens SQLite and applies the schema.
# Point it at a throwaway path so no build artefact reaches the image.
ENV DATABASE_PATH=/tmp/build.sqlite
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM node:22-slim AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    DATABASE_PATH=/data/beitenu.sqlite

# Don't run as root.
RUN groupadd --system --gid 1001 beitenu \
  && useradd --system --uid 1001 --gid beitenu beitenu

# The standalone bundle: server.js plus the modules Next traced, including the
# compiled better-sqlite3 binding. Copying the directory (not a glob) so hidden
# entries such as .next/ come with it.
COPY --from=build --chown=beitenu:beitenu /app/.next/standalone ./
COPY --from=build --chown=beitenu:beitenu /app/.next/static ./.next/static

# db.ts reads the schema from disk at startup, so it must ship with the image.
COPY --from=build --chown=beitenu:beitenu /app/db ./db

# Mount point for the persistent volume. Created here so the container still
# starts — and the health check still reports honestly — if none is attached.
RUN mkdir -p /data && chown beitenu:beitenu /data
VOLUME ["/data"]

USER beitenu
EXPOSE 3000

# Healthy only once the database is reachable, not merely once the process is
# up: a running server with no volume is not a working app.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>r.json()).then(d=>process.exit(d.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
