# syntax=docker/dockerfile:1
# fse console image: the fse image plus the console API (fse_ui) and the built SPA.
#
#   docker build -t fse:local ..
#   docker build -t fse-ui:local --build-arg FSE_IMAGE=fse:local .
#
# FSE_IMAGE is required: <registry>/fse:<sha> (CI) or fse:local
ARG FSE_IMAGE
# node:24-bookworm-slim (multi-arch index), pinned by digest.
ARG NODE_IMAGE=node:24-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run build
FROM ${FSE_IMAGE}
USER 0
COPY api/requirements.lock /tmp/ui-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/ui-requirements.lock && rm /tmp/ui-requirements.lock
COPY api/pyproject.toml /opt/fse-ui/api/pyproject.toml
COPY api/src /opt/fse-ui/api/src
# The engine image creates ~/.skylit-fse as a real directory so a Fargate cache
# mount keeps a writable parent. ln into that directory would create
# .skylit-fse/data and leave the directory on this image's read-only root, so
# the API cannot create ui/ui.sqlite and Caddy answers 502. Remove it first.
RUN pip install --no-cache-dir --no-deps -e /opt/fse-ui/api \
    && rm -rf /home/fse/.skylit-fse \
    && ln -s /data /home/fse/.skylit-fse \
    && chown -h 10001:10001 /home/fse/.skylit-fse
COPY --from=web /src/web/dist /opt/fse-ui/web
# a+rX: uid 10001 must read the console code even if the build context was 0600.
RUN chmod -R a+rX /opt/fse-ui
ENV FSE_UI_STATIC_DIR=/opt/fse-ui/web FSE_UI_HOME=/home/fse FSE_UI_PROJECT_DIR=/app \
    FSE_UI_BIND_HOST=127.0.0.1 FSE_UI_PORT=8080
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["fse-ui"]
