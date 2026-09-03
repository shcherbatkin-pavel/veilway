FROM node:22.13.1-bookworm-slim AS build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json tsconfig.app.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

FROM caddy:2.9.1-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /build/dist /srv/veilway
RUN addgroup -g 10001 veilway \
    && adduser -D -H -u 10001 -G veilway veilway \
    && chown -R 10001:10001 /srv/veilway /config /data
USER 10001:10001

