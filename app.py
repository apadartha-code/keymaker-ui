import os
import time
import math
import uuid
import threading
import numpy as np
from PIL import Image, ImageDraw
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename
import urllib.request

import base64
import getpass
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from imagegame.imagegame import ImageGame

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Thread-safe global memory structure
# Schema: { session_id: { "instance": ImageGame, "timestamp": float, "image_path": str } }
global_memory = {}
memory_lock = threading.Lock()


def touch_session(session_id):
    """Updates the last operation timestamp for the tracking session."""
    with memory_lock:
        if session_id in global_memory:
            global_memory[session_id]['timestamp'] = time.time()


@app.route('/')
def index():
    return render_template('index.html')


def derive_key(password: str, salt: bytes) -> bytes:
    """Derives a secure 256-bit AES key from the password and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000
    )
    return kdf.derive(password.encode())

@app.route("/challenge", methods=["POST"])
def handle_challenge():
    data = request.get_json() or {}
    client_salt_b64 = data.get("salt")

    if not client_salt_b64:
        return jsonify({"error": "Missing parameters"}), 400

    try:
        # Decode the salt and derive the matching key
        salt = base64.b64decode(client_salt_b64)
        derived_key = derive_key(app.config["STARTUP_PASSWORD"], salt)

        # Pad plaintext to 16-byte block size for AES-CBC
        plaintext = app.config["SERVER_UUID"]
        padded_text = plaintext.encode()
        padding_len = 16 - (len(padded_text) % 16)
        padded_text += bytes([padding_len]) * padding_len

        # Generate a unique Initialization Vector (IV) for this response
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


# Step 0: Session Initialization
@app.route('/api/session', methods=['POST'])
def create_session():
    session_id = uuid.uuid4().hex
    with memory_lock:
        global_memory[session_id] = {
            'instance': None,
            'timestamp': time.time(),
            'image_path': None
        }
    return jsonify({'session_id': session_id})


# Step 1: Disk Upload
@app.route('/api/upload', methods=['POST'])
def upload_image():
    session_id = request.form.get('session_id')
    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid or missing session_id'}), 400
    
    touch_session(session_id)
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file element provided'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    filename = secure_filename(f"{session_id}_{file.filename}")
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    with memory_lock:
        global_memory[session_id]['image_path'] = filepath

    # Return local routing url
    return jsonify({'local_url': f"/api/image/file/{filename}"})


# Step 1: Process and Initialize Game
def download_and_prepare_worker(session_id, url_or_path):
    """
    Background worker that downloads remote images, normalizes their scale, 
    and triggers the game state calculation matrix.
    """
    with memory_lock:
        instance = global_memory[session_id]['instance']
        filepath = global_memory[session_id]['image_path']

    # Check if we need to fetch the resource over the network
    if url_or_path.startswith(('http://', 'https://')):
        try:
            filename = f"download_{session_id}.png"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

            # Spoof a standard browser User-Agent to bypass CDNs/WAFs blocking Python
            req = urllib.request.Request(
                url_or_path, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            )
            
            # Download the binary data stream
            with urllib.request.urlopen(req, timeout=15) as response:
                with open(filepath, 'wb') as out_file:
                    out_file.write(response.read())

            '''
            # Normalize the image dimensions to 400x400 to match our canvas and dummy mask grid
            with Image.open(filepath) as img:
                img_resized = img.resize((400, 400), Image.Resampling.LANCZOS)
                img_resized.save(filepath, "PNG")
            '''

            # Update session mapping so /api/image knows what to serve
            with memory_lock:
                global_memory[session_id]['image_path'] = filepath

        except Exception as e:
            print(f"CRITICAL: Failed downloading internet resource for session {session_id}. Error: {e}")
            # The worker will still let prepare run or fail gracefully rather than locking the poll
    
    # Trigger the heavy analysis engine processing simulation
    if instance:
        try:
            instance.prepare(filepath)
        except Exception as e:
            print(f"Error during instance preparation: {e}")


@app.route('/api/process', methods=['POST'])
def process_image():
    data = request.json or {}
    session_id = data.get('session_id')
    url = data.get('url')

    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400
    if not url:
        return jsonify({'error': 'Missing Image URL path'}), 400

    touch_session(session_id)

    instance = ImageGame(url)
    
    with memory_lock:
        global_memory[session_id]['instance'] = instance

    # Pass the heavy downloading and matrix configuration to our async worker
    thread = threading.Thread(target=download_and_prepare_worker, args=(session_id, url))
    thread.start()

    return jsonify({'status': 'processing'})


# Step 1: Polling Endpoint
@app.route('/api/poll', methods=['GET'])
def poll_status():
    session_id = request.args.get('session_id')
    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    instance = global_memory[session_id]['instance']
    
    if not instance:
        return jsonify({'ready': False})

    return jsonify({'ready': instance.ready()})


# Step 2: Retrieve Image Data
@app.route('/api/image', methods=['GET'])
def get_image():
    session_id = request.args.get('session_id')
    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    filepath = global_memory[session_id].get('image_path')

    # If no local file path was processed via step 1 upload, output an elegant standalone placeholder canvas
    if not filepath or not os.path.exists(filepath):
        placeholder_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{session_id}_placeholder.png")
        if not os.path.exists(placeholder_path):
            img = Image.new('RGB', (400, 400), color=(73, 109, 137))
            d = ImageDraw.Draw(img)
            d.text((130, 190), "Image Processing Base", fill=(255, 255, 255))
            img.save(placeholder_path)
        filepath = placeholder_path

    return send_file(filepath, mimetype='image/png')


# Step 2: Retrieve Label Mask Data
@app.route('/api/mask', methods=['GET'])
def get_mask():
    session_id = request.args.get('session_id')
    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    instance = global_memory[session_id]['instance']
    
    if not instance or not instance.ready():
        return jsonify({'error': 'Instance not initialized or ready'}), 400

    try:
        mask_matrix = instance.mask()
        return jsonify({'mask': mask_matrix.tolist()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Step 3: Password Update Verification Flow
@app.route('/api/step3', methods=['POST'])
def step3_submit():
    data = request.json or {}
    session_id = data.get('session_id')
    password = data.get('password')

    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    instance = global_memory[session_id]['instance']

    try:
        label_sequence = instance.update(str(password))
        return jsonify({'sequence': label_sequence})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Step 4: Click Sequence Tracking Data Flow
@app.route('/api/step4', methods=['POST'])
def step4_submit():
    data = request.json or {}
    session_id = data.get('session_id')
    labels = data.get('labels')

    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400
    if not isinstance(labels, list):
        return jsonify({'error': 'Labels must be delivered as an ordered array sequence'}), 400

    touch_session(session_id)
    instance = global_memory[session_id]['instance']

    try:
        instance.update(labels)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Step 5: Result Retrieval Flow
@app.route('/api/step5', methods=['GET'])
def step5_result():
    session_id = request.args.get('session_id')
    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    instance = global_memory[session_id]['instance']

    try:
        result_string = instance.result()
        return jsonify({'result': result_string})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("=== Secure Startup Initialization ===")
    
    # 1. Halt execution to securely prompt user for input
    secret_input = getpass.getpass("Enter the server verification password: ")
    
    if not secret_input.strip():
        print("Error: Password cannot be empty. Aborting startup.")
        exit(1)
        
    # 2. Store the string securely inside Flask config
    app.config["STARTUP_PASSWORD"] = secret_input
    # 2.1 Generate a server identity to be encrypted using the secret
    # for a challenge.
    server_id = uuid.uuid4().hex
    app.config["SERVER_UUID"] = server_id
    print("Server identity initialized. Starting HTTPS server...")
    
    # 3. Bind to the local port using your self-signed certificate
    # Note: Turn off debug = True - it will start again without the password.
    app.run(host='0.0.0.0', port=5001, ssl_context=('config/cert.pem', 'config/key.pem'))