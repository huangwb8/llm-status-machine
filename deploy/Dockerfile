ARG NODE_IMAGE=node:24-alpine

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
RUN apk add --no-cache git
COPY package*.json ./
RUN npm ci

FROM deps AS build
COPY . .
RUN npm test
RUN npm run build

FROM ${NODE_IMAGE} AS prod-deps
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev && npm cache clean --force

FROM ${NODE_IMAGE} AS runtime
RUN apk add --no-cache \
    bash \
    ca-certificates \
    git \
    openssh-client \
    su-exec \
    tini \
  && addgroup -S app \
  && adduser -S -G app -h /app app

WORKDIR /app

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=4317 \
    DATA_DIR=/app/data \
    RUNS_DIR=/app/data/runs

COPY --from=prod-deps --chown=app:app /app/node_modules ./node_modules
COPY --chown=app:app package*.json ./
COPY --chown=app:app server ./server
COPY --from=build --chown=app:app /app/dist ./dist
COPY --chmod=0755 deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/data /workspaces \
  && chown -R app:app /app /workspaces

EXPOSE 4317

ENTRYPOINT ["tini", "--", "docker-entrypoint.sh"]
CMD ["node", "server/index.js"]
