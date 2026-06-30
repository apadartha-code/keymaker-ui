import os
import time
import uuid
import threading
import urllib.request
from flask import Blueprint, request, jsonify, render_template, send_file, session
from werkzeug.utils import secure_filename
from imagegame.imagegame import ImageGame

# Define the standalone tool package Blueprint
keymaker_bp = Blueprint(
    'keymaker_ui',
    __name__,
    template_folder='templates',
    static_folder='static'
)

# The following keys are exposed to the main app to
# attach custom objects for the session.
KM_SESSION_KEY = 'keymaker_id'
TRANSCODER_KEY = 'transcoder'
BKEND_VAULT_KEY = 'crypt'

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Thread-safe tool memory structure isolated to the blueprint scope
class KeymakerSessions:
    def __init__(self):
        self._memory_lock = threading.Lock()
        self._session_data = {}

    def exists(self, session_id):
        return session_id in self._session_data

    def touch_session(self, session_id):
        with self._memory_lock:
            if session_id in self._session_data:
                self._session_data[session_id]['timestamp'] = time.time()

    def init_session(self, session_id):
        # Check for session key before creating...
        with self._memory_lock:
            if session_id not in self._session_data:
                print("DEBUG: Initializing session for:", session_id)
                self._session_data[session_id] = {
                    'instance': None,
                    'timestamp': time.time(),
                    'image_path': None,
                    TRANSCODER_KEY: None,
                    BKEND_VAULT_KEY: None
                }

    def update_session(self, session_id, key, value):
        self.touch_session(session_id)
        with self._memory_lock:
            if session_id in self._session_data:
                self._session_data[session_id][key] = value

    def get_session_val(self, session_id, key):
        self.touch_session(session_id)
        with self._memory_lock:
            if session_id in self._session_data:
                return self._session_data[session_id][key]
        return None

keymaker_sessions = KeymakerSessions()

# Custom UI Route for the Tool (Your upload screen up to "Show me the money")
@keymaker_bp.route('/ui')
def tool_workspace():
    # Capture target callback pointing to the main app's verification display
    session['tool_callback'] = request.args.get('callback', '/')
    return render_template('keymaker.html') # This will be the isolated Tool HTML template
    
@keymaker_bp.route('/api/session', methods=['POST'])
def create_session():
    # If a session id already exists, don't create a new UUID.
    session_id = session.get(KM_SESSION_KEY)
    if not session_id:
        session_id = uuid.uuid4().hex
        session[KM_SESSION_KEY] = session_id
    keymaker_sessions.init_session(session_id)
    return jsonify({'session_id': session_id})

@keymaker_bp.route('/api/upload', methods=['POST'])
def upload_image():
    session_id = request.form.get('session_id')
    if not session_id or not keymaker_sessions.exists(session_id):
        return jsonify({'error': 'Invalid or missing session_id'}), 400
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file element provided'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    filename = secure_filename(f"{session_id}_{file.filename}")
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    keymaker_sessions.update_session(session_id, 'image_path', filepath)

    return jsonify({'local_url': f"/keymaker/api/image/file/{filename}"})

def download_and_prepare_worker(session_id, url_or_path):
    if not keymaker_sessions.exists(session_id): return
    instance = keymaker_sessions.get_session_val(session_id, 'instance')
    filepath = keymaker_sessions.get_session_val(session_id, 'image_path')

    if url_or_path.startswith(('http://', 'https://')):
        try:
            filename = f"download_{session_id}.png"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            req = urllib.request.Request(
                url_or_path, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows)'}
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                with open(filepath, 'wb') as out_file:
                    out_file.write(response.read())

            keymaker_sessions.update_session(session_id, 'image_path', filepath)
        except Exception as e:
            print(f"Download failed for session {session_id}: {e}")
    
    if instance:
        try:
            instance.prepare(filepath)
        except Exception as e:
            print(f"Error during instance preparation: {e}")

def decodeStringParam(session_id, encoded_param):
    transcoder = keymaker_sessions.get_session_val(session_id, TRANSCODER_KEY)
    param = ""
    # print("DEBUG: Original encoded parameter:", encoded_param)
    if transcoder:
        param = transcoder.decode_str(encoded_param)
    # print("DEBUG: decoded parameter:", param)
    return param

def decodeObjectParam(session_id, encoded_param):
    transcoder = keymaker_sessions.get_session_val(session_id, TRANSCODER_KEY)
    obj = None
    print("DEBUG: Original encoded parameter:", encoded_param)
    if transcoder:
        obj = transcoder.decode_obj(encoded_param)
    print("DEBUG: decoded parameter:", str(obj))
    return obj

@keymaker_bp.route('/api/process', methods=['POST'])
def process_image():
    data = request.json or {}
    session_id = data.get('session_id')
    url = decodeStringParam(session_id, data.get('url'))

    if not session_id or not keymaker_sessions.exists(session_id):
        return jsonify({'error': 'Invalid session identifier'}), 400
    if not url:
        return jsonify({'error': 'Missing Image URL path'}), 400

    instance = ImageGame(url)

    keymaker_sessions.update_session(session_id, 'instance', instance)

    thread = threading.Thread(target=download_and_prepare_worker, args=(session_id, url))
    thread.start()
    return jsonify({'status': 'processing'})

@keymaker_bp.route('/api/poll', methods=['GET'])
def poll_status():
    session_id = request.args.get('session_id')
    if not session_id or not keymaker_sessions.exists(session_id):
        return jsonify({'error': 'Invalid session identifier'}), 400

    instance = keymaker_sessions.get_session_val(session_id, 'instance')
    if not instance:
        return jsonify({'ready': False})
    return jsonify({'ready': instance.ready()})

@keymaker_bp.route('/api/image', methods=['GET'])
def get_image():
    session_id = request.args.get('session_id')
    if not session_id or not keymaker_sessions.exists(session_id):
        return jsonify({'error': 'Invalid session identifier'}), 400

    filepath = keymaker_sessions.get_session_val(session_id, 'image_path')
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'Image not found'}), 404

    return send_file(filepath, mimetype='image/png')

@keymaker_bp.route('/api/mask', methods=['GET'])
def get_mask():
    session_id = request.args.get('session_id')
    if not session_id or not keymaker_sessions.exists(session_id):
        return jsonify({'error': 'Invalid session identifier'}), 400

    instance = keymaker_sessions.get_session_val(session_id, 'instance')
    if not instance or not instance.ready():
        return jsonify({'error': 'Instance not initialized'}), 400

    try:
        mask_matrix = instance.mask()
        return jsonify({'mask': mask_matrix.tolist()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@keymaker_bp.route('/api/step3', methods=['POST'])
def step3_submit():
    data = request.json or {}
    session_id = data.get('session_id')

    if not session_id or not keymaker_sessions.exists(session_id):
        return jsonify({'error': 'Invalid session identifier'}), 400

    instance = keymaker_sessions.get_session_val(session_id, 'instance')
    password = decodeStringParam(session_id, data.get('password'))
    try:
        label_sequence = instance.update(str(password))
        return jsonify({'sequence': label_sequence})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@keymaker_bp.route('/api/step4', methods=['POST'])
def step4_submit():
    data = request.json or {}
    session_id = data.get('session_id')
    # labels = data.get('labels')

    if not session_id or not keymaker_sessions.exists(session_id):
        return jsonify({'error': 'Invalid session identifier'}), 400

    instance = keymaker_sessions.get_session_val(session_id, 'instance')
    labels = decodeObjectParam(session_id, data.get('labels'))
    try:
        instance.update(labels)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# The Critical Handoff point ("Show me the money")
@keymaker_bp.route('/api/step5', methods=['POST'])
def step5_finalize_and_redirect():
    data = request.json or {}
    session_id = data.get('session_id')

    if not session_id or not keymaker_sessions.exists(session_id):
        return jsonify({'error': 'Invalid session identifier'}), 400

    instance = keymaker_sessions.get_session_val(session_id, 'instance')
    try:
        # Retrieve raw text from image game processing
        raw_result_string = instance.result()
        
        # Drop the raw tool output securely inside the backend memory footprint
        vault = keymaker_sessions.get_session_val(session_id, BKEND_VAULT_KEY)
        vault.save_encoding(bytearray.fromhex(raw_result_string))
        
        # Pop the tracking link pointing back to the core platform UI context
        target_callback = session.pop('tool_callback', '/')
        
        # Inform frontend SPA to break execution and return to the main application context
        return jsonify({
            'status': 'redirect',
            'redirect_url': target_callback
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500