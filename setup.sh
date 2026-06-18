#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Creating virtual environment..."
python3 -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing requirements..."
pip install -r requirements.txt

echo "Setup complete! Run 'source venv/bin/activate && python app.py' to start the application."