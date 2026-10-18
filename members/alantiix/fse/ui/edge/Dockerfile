# syntax=docker/dockerfile:1
# fse-edge: Caddy with the Route 53 DNS provider (ACME DNS-01), in front of the console API.
#
#   docker build -t fse-edge:local .
#
# caddy:2.10.2-builder and caddy:2.10.2 (multi-arch indexes), pinned by digest.
# caddy-dns/route53 v1.6.2 supports hosted_zone_id and is built against caddy v2.10.2.
# Go module integrity comes from the Go checksum database.
FROM caddy:2.10.2-builder@sha256:01668408cc26e2e00c9d067c30cb43b2ba14ad1f2808beda55503cb2a31f59dc AS build
RUN xcaddy build --with github.com/caddy-dns/route53@v1.6.2
FROM caddy:2.10.2@sha256:c3d7ee5d2b11f9dc54f947f68a734c84e9c9666c92c88a7f30b9cba5da182adb
COPY --from=build /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
