FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends g++ clang libboost1.83-dev libicu-dev libjemalloc-dev python3 ca-certificates && rm -rf /var/lib/apt/lists/*
