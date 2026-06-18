#!/bin/bash

mkdir config
openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365
mv cert.pem key.pem config/

echo "Generated self signed cert in config folder..."
ls config/