# syntax=docker/dockerfile:1
# fse container image: linux/arm64, non-root (uid 10001), tini as PID 1.
#
#   docker build --platform linux/arm64 -t fse:local .
#   docker run --rm fse:local fse --help
#
# The project is copied to /app and installed editable, so fse finds its Project
# folder (your plugins, calendars/, configs/). See docker/entrypoint.sh for the
# runner and batch modes. .dockerignore keeps .env and every local output out.

# python:3.14-slim (multi-arch index), pinned by digest.
ARG PYTHON_IMAGE=python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

# ---------------------------------------------------------------- AWS CLI v2
FROM ${PYTHON_IMAGE} AS awscli
ARG AWSCLI_VERSION=2.36.8
# Fingerprint of the AWS CLI Team key (docker/awscli-pubkey.asc, from the AWS CLI install docs).
ARG AWSCLI_SIGNER_FPR=FB5DB77FD5C118B80511ADA8A6310ACC4672475C
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg unzip \
    && rm -rf /var/lib/apt/lists/*
COPY docker/awscli-pubkey.asc /tmp/awscli-pubkey.asc
WORKDIR /tmp/awscli
RUN set -eu; \
    arch="$(uname -m)"; \
    case "$arch" in \
      aarch64|x86_64) ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://awscli.amazonaws.com/awscli-exe-linux-${arch}-${AWSCLI_VERSION}.zip"; \
    curl -fsSLo awscliv2.zip "$url"; \
    curl -fsSLo awscliv2.zip.sig "$url.sig"; \
    GNUPGHOME="$(mktemp -d)"; export GNUPGHOME; \
    gpg --batch --import /tmp/awscli-pubkey.asc; \
    gpg --batch --status-fd 1 --verify awscliv2.zip.sig awscliv2.zip > verify.txt; \
    grep -q "^\[GNUPG:\] VALIDSIG ${AWSCLI_SIGNER_FPR} " verify.txt; \
    unzip -q awscliv2.zip; \
    ./aws/install --install-dir /usr/local/aws-cli --bin-dir /usr/local/bin; \
    # `aws help` examples and shell completion are never used in the container (~35 MB).
    rm -rf /usr/local/aws-cli/v2/current/dist/awscli/examples \
        /usr/local/aws-cli/v2/current/dist/aws_completer; \
    /usr/local/bin/aws --version

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HOME=/home/fse
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --user-group --create-home --home-dir /home/fse \
       --shell /usr/sbin/nologin fse \
    && mkdir -p \
        /home/fse/.skylit-fse/cache \
        /home/fse/.skylit-fse/runs \
        /home/fse/.skylit-fse/configs \
        /home/fse/.skylit-fse/ui/uploads \
        /home/fse/.skylit-fse/research \
    && chown -R 10001:10001 /home/fse
COPY --from=awscli /usr/local/aws-cli /usr/local/aws-cli
RUN ln -s /usr/local/aws-cli/v2/current/bin/aws /usr/local/bin/aws
COPY docker/requirements-runtime.lock /tmp/requirements-runtime.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-runtime.lock \
    && rm /tmp/requirements-runtime.lock
COPY . /app
# a+rX: the build context may carry 0600 files (a restrictive umask at checkout);
# the image runs as uid 10001, which must read /app.
RUN chmod -R a+rX /app \
    && pip install --no-cache-dir --no-deps -e /app \
    && install -m 0755 /app/docker/entrypoint.sh /usr/local/bin/fse-entrypoint \
    && test ! -e /app/.env
WORKDIR /app
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/fse-entrypoint"]
CMD ["runner"]
