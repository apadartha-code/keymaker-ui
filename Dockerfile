# Use an official lightweight Python image
FROM python:3.11-slim

# Install system dependencies (git for cloning, openssl/bash for cert.sh if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    bash \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Upgrade pip
RUN pip install --upgrade pip

# 1. Clone and install the backend peer dependency (keymaker-proto)
# We place it outside the main /app folder to mimic your peer folder setup
WORKDIR /workspace
RUN git clone https://github.com/apadartha-code/keymaker-proto.git \
    && cd keymaker-proto \
    && pip install -e .

# 2. Copy the current project (keymaker-ui) into /app
WORKDIR /app
COPY . /app

# Install the current project in editable mode (as per your script)
RUN pip install -e .

# Make the cert script executable and run it to generate self-signed certs
RUN chmod +x cert.sh && ./cert.sh

# Expose the Flask port
EXPOSE 5000

# Run the application
CMD ["python", "app.py"]