#!/bin/bash
# Self-signed TLS certs for the production compose (dev/lab use).
# For a real deployment replace certs/dashboard.{crt,key} with CA-issued
# ones (e.g. certbot certonly --standalone -d <hostname>).
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HERE/certs"
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout "$HERE/certs/dashboard.key" \
  -out "$HERE/certs/dashboard.crt" \
  -subj "/CN=patrolbot-dashboard" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
echo "wrote $HERE/certs/dashboard.{crt,key}"
