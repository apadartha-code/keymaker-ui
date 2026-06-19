#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Create config directory if it doesn't exist
mkdir -p config

echo "Generating self-signed cert non-interactively..."

# The -subj string provides all necessary answers to the prompts
openssl req -x509 -newkey rsa:4096 -nodes \
  -out cert.pem \
  -keyout key.pem \
  -days 365 \
  -subj "/C=XX/ST=State/L=City/O=Organization/OU=Development/CN=127.0.0.1"

# Move the generated certificates to the config directory
mv cert.pem key.pem config/

echo "Generated self-signed cert in config folder..."
ls config/