# ==============================================================================
# TokenSafe B2B Enterprise On-Premise AI Proxy
# Multi-stage production container with Nuitka C-compilation for IP protection
# ==============================================================================

FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    patchelf \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir nuitka

COPY src/ /build/src/
COPY proto/ /build/proto/

# Compile Python proxy entrypoint into a high-performance C native binary
RUN python3 -m nuitka \
    --standalone \
    --onefile \
    --remove-output \
    --output-dir=/dist \
    --output-filename=tokensafe-proxy \
    src/proxy.py

# ==============================================================================
# Minimal Production Runner
# ==============================================================================
FROM debian:bookworm-slim AS runner

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled native binary from builder stage
COPY --from=builder /dist/tokensafe-proxy /app/tokensafe-proxy

ENV PORT=8765
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8765/health || exit 1

ENTRYPOINT ["/app/tokensafe-proxy"]
CMD ["8765"]
