# Frontend for visual memory encoder

This repository is part of a project that bridges the gap between human visual entropy and binary data. Utilizing an interactive visual memory sequence, the software captures human cognitive interaction and converts it into a distinct binary structure.

This frontend application is designed to communicate with the core processing backend engine ([keymaker-proto](https://github.com/apadartha-code/keymaker-proto)). 

> **ℹ️ Architecture Note:** While this user interface is openly licensed, please note that the required `keymaker-proto` backend operates under a separate, restrictive non-commercial license.

⚠️ **Disclaimers:** This codebase is currently an early-stage academic proof of concept. While the underlying logic functions as intended, the implementation is research-grade, fragile, and not optimized for production environments.

---

## 🚀 Getting Started (Non-Commercial Research)

**Note**: The app requires a startup password for the UIs to validate. If it is ran non-interactively as a docker, a nonce.txt file will need to be mapped as shown below.

### Terminal:
#### Prerequisites
* **Python 3.8+** (Ensure this matches your backend requirements)

#### Installation & Execution
```bash
# Clone the repository
git clone https://github.com/apadartha-code/keymaker-ui.git

# Navigate into the project folder
cd keymaker-ui

# (Optional) Ensure scripts are executable
chmod +x setup.sh cert.sh

# Set up the virtual environment and the certificates
./setup.sh

# Follow the on-screen instructions to activate the app.
# The interface will listen locally on port 5000 (0.0.0.0:5000) over https.
# Ignore the browser warnings for certificate verification and proceed.
```

### Docker:
#### Prerequisites
* Access to **docker** group for running docker commands.

### Building the image
```bash
# Clone the repository
git clone https://github.com/apadartha-code/keymaker-ui.git

# Navigate into the project folder
cd keymaker-ui

# Build the image
docker build -t keymaker-ui .
```

#### Running interactively (password from console)
```bash
# Run the image
docker run -it --rm -p 5000:5000 --name keymaker keymaker-ui
# The interface will listen locally on port 5000 (0.0.0.0:5000) over https.
# Ignore the browser warnings for certificate verification and proceed.
```

#### Running in detached mode (mapped nonce file)
```bash
docker run -d --rm -p 5000:5000 -v $(pwd)/nonce.txt:/app/nonce.txt --name keymaker keymaker-ui --fifo-path /tmp/keymaker_fifo
docker exec -it keymaker /app/initnonce.sh
```

---

## 📜 Legal Status & Licensing

* **Frontend UI License:** This user interface repository is licensed under the permissive **Apache 2.0 License**. You are free to modify, distribute, and build upon this frontend architecture.
* **Backend Dependency Restriction:** The core backend infrastructure ([keymaker-proto](https://github.com/apadartha-code/keymaker-proto)) utilized by this interface is strictly bound to the **PolyForm Noncommercial License 1.0.0**. Commercial or for-profit deployment of the complete ecosystem requires an explicit commercial arrangement with the author.
