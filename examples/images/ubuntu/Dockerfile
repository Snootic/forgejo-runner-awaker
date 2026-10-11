FROM node:24-bookworm-slim AS node
FROM ubuntu:26.04

COPY --from=node /usr/local /usr/local

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
