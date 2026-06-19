import os
import uuid
import base64
import getpass
from flask import Flask, request, jsonify, render_template, session
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Import the tool package blueprint
from keymaker_ui import keymaker_bp

app = Flask(__name__)
app.secret_key = os.urandom(24) # Shared backend cookie verification identity
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Mount the Keymaker tool workspace under /keymaker prefix
app.register_blueprint(keymaker_bp, url_prefix='/keymaker')

def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000
    )
    return kdf.derive(password.encode())

# The platform index (The core verification dashboard wrapper)
@app.route('/')
def main_application_dashboard():
    # Read raw output securely dropped inside backend session memory by the tool blueprint
    raw_output = session.pop('raw_tool_output', None)
    verified_encoding = None
    
    if raw_output:
        # MAIN APPLICATION RESPONSIBILITY: Run the secure server verification rules
        # Format or execute calculations with the raw matrix text safely on the server
        # verified_encoding = f"VERIFIED_SECURE_HASH_{raw_output.upper()}"
        verified_encoding = raw_output.upper()

    return render_template('index.html', encoding=verified_encoding)

@app.route("/challenge", methods=["POST"])
def handle_challenge():
    data = request.get_json() or {}
    client_salt_b64 = data.get("salt")
    if not client_salt_b64:
        return jsonify({"error": "Missing parameters"}), 400

    try:
        salt = base64.b64decode(client_salt_b64)
        derived_key = derive_key(app.config["STARTUP_PASSWORD"], salt)

        plaintext = app.config["SERVER_UUID"]
        padded_text = plaintext.encode()
        padding_len = 16 - (len(padded_text) % 16)
        padded_text += bytes([padding_len]) * padding_len

        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(derived_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_text) + encryptor.finalize()

        return jsonify({
            "iv": base64.b64encode(iv).decode('utf-8'),
            "ciphertext": base64.b64encode(ciphertext).decode('utf-8'),
            "plaintext": plaintext
        }), 200
    except Exception:
        return jsonify({"error": "Processing failed"}), 500

if __name__ == '__main__':
    print("=== Secure Startup Initialization ===")
    # Define the path to the nonce file
    nonce_file_path = "nonce.txt"
    secret_input = ""
    try:
        # Check if file exists and has content before opening to prevent unnecessary locks
        if os.path.exists(nonce_file_path) and os.path.getsize(nonce_file_path) > 0:
            with open(nonce_file_path, "r+") as f:
                # Read the first line
                line = f.readline()
                
                # If line is not empty, strip the trailing newline/whitespaces
                if line:
                    secret_input = line.rstrip('\r\n')
                
                # Rewind to the beginning of the file and truncate it to 0 bytes
                f.seek(0)
                f.truncate(0)
    except Exception as e:
        # Fail-safe: capture any unexpected OS errors without crashing the Flask app
        print(f"Error handling nonce file: {e}")
        secret_input = ""
    
    if len(secret_input) == 0:
        # Interactive initialization of verification string.
        secret_input = getpass.getpass("Enter the server verification password: ")
        if not secret_input.strip():
            print("Error: Password cannot be empty. Aborting startup.")
            exit(1)
        
    app.config["STARTUP_PASSWORD"] = secret_input
    app.config["SERVER_UUID"] = uuid.uuid4().hex
    print("Server identity initialized. Starting HTTPS server...")
    
    app.run(host='0.0.0.0', port=5000, ssl_context=('config/cert.pem', 'config/key.pem'))