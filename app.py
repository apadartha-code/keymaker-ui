import os
import sys
import uuid
import base64
import random
from flask import Flask, request, jsonify, render_template, session

from crypto import read_password_via_syscall, SecureMemoryPassword, aes_cbc_encrypt, SecureKeyScope, mutable_urandom, TransCrypter

# Import the tool package blueprint
from keymaker_ui import keymaker_bp, KM_SESSION_KEY, TRANSCODER_KEY, BKEND_VAULT_KEY, keymaker_sessions

app = Flask(__name__)
app.secret_key = os.urandom(24) # Shared backend cookie verification identity
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Mount the Keymaker tool workspace under /keymaker prefix
app.register_blueprint(keymaker_bp, url_prefix='/keymaker')

# Declare the global master key here.
master_key = None


# The platform index (The core verification dashboard wrapper)
@app.route('/')
def main_application_dashboard():
    if not session.get(KM_SESSION_KEY):
        session_id = uuid.uuid4().hex
        keymaker_sessions.init_session(session_id)
        session[KM_SESSION_KEY] = session_id
    return render_template('index.html')

def encodeStringParam(session_id, param):
    transcoder = keymaker_sessions.get_session_val(session_id, TRANSCODER_KEY)
    encoded_param = ""
    # print("DEBUG: Original parameter:", param)
    if transcoder:
        encoded_param = transcoder.encode_str(param)
    # print("DEBUG: encoded parameter:", encoded_param)
    return encoded_param

@app.route("/api/encoding")
def get_encoding():
    # Read raw output securely dropped inside backend session memory by the tool blueprint
    session_id = session[KM_SESSION_KEY]
    vault = keymaker_sessions.get_session_val(session_id, BKEND_VAULT_KEY)
    verified_encoding = ""
    raw_output_bytes = bytearray()
    if vault:
        try:
            raw_output_bytes = vault.get_be_secret()
            raw_output = raw_output_bytes.hex()
            if raw_output:
                # MAIN APPLICATION RESPONSIBILITY: Run the secure server verification rules
                # Format or execute calculations with the raw matrix text safely on the server
                # verified_encoding = f"VERIFIED_SECURE_HASH_{raw_output.upper()}"
                verified_encoding = encodeStringParam(session_id, raw_output.upper())
        except Exception as e:
            print("Error:", str(e))
            return jsonify({"error": "Retrieval failed"}), 500
        finally:
            for i in range(len(raw_output_bytes)): raw_output_bytes[i] = 0

    return jsonify({"encoding": verified_encoding }), 200

@app.route("/result")
def show_result():
    return render_template('result.html')

@app.route("/challenge", methods=["POST"])
def handle_challenge():
    """
    Respond with a plaintext and encrypted (with the server startup
    nonce) version of the server UUID. The requestor can use their
    own password and salt to decrypt and match. Also send a random
    32-byte (base64 encoded) string to be used as a session password,
    encoded with the same key.
    """
    data = request.get_json() or {}
    client_salt_b64 = data.get("salt")
    if not client_salt_b64:
        return jsonify({"error": "Missing parameters"}), 400

    try:
        salt = base64.b64decode(client_salt_b64)
        with SecureKeyScope(master_key.decrypt(app.config["STARTUP_PASSWORD"]), salt) as derived_key:
            plaintext = app.config["SERVER_UUID"]
            cipherbytes = aes_cbc_encrypt(derived_key, plaintext.encode())
            iv = cipherbytes[:16]
            ciphertext = cipherbytes[16:]

            session_secret = mutable_urandom(32)
            cipherbytes2 = aes_cbc_encrypt(derived_key, session_secret)
            iv2 = cipherbytes2[:16]
            ciphertext2 = cipherbytes2[16:]

        # Create a TransCrypter and attach it to the session for the
        # blueprint to use when it's invoked.
        encoding_helper = TransCrypter(master_key, mask_seed = random.randint(0, 2**64))
        encoding_helper.set_fe_secret(session_secret) # This will wipe out the session_secret
        session_id = session[KM_SESSION_KEY]
        # For use in communicating with the frontend.
        keymaker_sessions.update_session(session_id, TRANSCODER_KEY, encoding_helper)
        # For saving the final encoding.
        keymaker_sessions.update_session(session_id, BKEND_VAULT_KEY, encoding_helper)

        return jsonify({
            "iv": base64.b64encode(iv).decode('utf-8'),
            "ciphertext": base64.b64encode(ciphertext).decode('utf-8'),
            "iv2": base64.b64encode(iv2).decode('utf-8'),
            "ciphertext2": base64.b64encode(ciphertext2).decode('utf-8'),
            "plaintext": plaintext
        }), 200
    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": "Processing failed"}), 500
    finally:
        for i in range(len(session_secret)): session_secret[i] = 0

if __name__ == '__main__':
    # 1. Create the session master key that will keep everything else encrypted.
    master_key = SecureMemoryPassword(mutable_urandom(32))
    # Note! Do not put this under some app.config key,
    # because that makes it easily discoverable by
    # memory scanning for the key text.

    # 2. Get the session secret nonce for server validation.
    print("=== Secure Startup Initialization ===")
    secret_input = read_password_via_syscall("Enter the server verification password: ")
    if len(secret_input) == 0:
        print("Error: Password cannot be empty. Aborting startup.", file = sys.stderr)
        exit(1)
        
    try:
        app.config["STARTUP_PASSWORD"] = master_key.encrypt(secret_input)
    finally:
        for i in range(len(secret_input)): secret_input[i] = 0 # Erase the plaintext.

    # 3. Start the server.
    app.config["SERVER_UUID"] = uuid.uuid4().hex
    print("Server identity initialized. Starting HTTPS server...")
    
    app.run(host='0.0.0.0', port=5000, ssl_context=('config/cert.pem', 'config/key.pem'))