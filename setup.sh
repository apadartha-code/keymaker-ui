#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Creating virtual environment..."
python3 -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing package..."
pip install -e .

echo "Downloading backend (keymaker-proto) in peer folder..."
pushd .. && git clone https://github.com/apadartha-code/keymaker-proto.git

echo "Installing backend package in current virtual environment..."
cd keymaker-proto && pip install -e .
popd

echo "Generating self-signed certs..."
./cert.sh

echo "Setup complete!"
echo "Run 'source venv/bin/activate && python app.py' to start the application."
echo "Then go to 'https://127.0.0.1:5000/' in your browser."