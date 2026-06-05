# Build stage
ARG CADDY_VERSION=latest
FROM caddy:${CADDY_VERSION}-builder AS builder

ARG CADDY_EXTRA_MODULES=""
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    set -e; \
    extra_with=""; \
    if [ -n "$CADDY_EXTRA_MODULES" ]; then \
        old_ifs="$IFS"; IFS=','; \
        for spec in $CADDY_EXTRA_MODULES; do \
            extra_with="$extra_with --with $spec"; \
        done; \
        IFS="$old_ifs"; \
    fi; \
    xcaddy build \
        --with github.com/caddy-dns/cloudflare \
        --with github.com/WeidiDeng/caddy-cloudflare-ip \
        --with github.com/fvbommel/caddy-combine-ip-ranges \
        --with github.com/caddyserver/replace-response \
        $extra_with

# Final stage
FROM caddy:${CADDY_VERSION}

# Copy the custom-built Caddy binary
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
