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

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Thread-safe tool memory structure isolated to the blueprint scope
global_memory = {}
memory_lock = threading.Lock()

def touch_session(session_id):
    with memory_lock:
        if session_id in global_memory:
            global_memory[session_id]['timestamp'] = time.time()

# Custom UI Route for the Tool (Your upload screen up to "Show me the money")
@keymaker_bp.route('/ui')
def tool_workspace():
    # Capture target callback pointing to the main app's verification display
    session['tool_callback'] = request.args.get('callback', '/')
    return render_template('keymaker.html') # This will be the isolated Tool HTML template

@keymaker_bp.route('/api/session', methods=['POST'])
def create_session():
    session_id = uuid.uuid4().hex
    with memory_lock:
        global_memory[session_id] = {
            'instance': None,
            'timestamp': time.time(),
            'image_path': None
        }
    return jsonify({'session_id': session_id})

@keymaker_bp.route('/api/upload', methods=['POST'])
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
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    with memory_lock:
        global_memory[session_id]['image_path'] = filepath

    return jsonify({'local_url': f"/keymaker/api/image/file/{filename}"})

def download_and_prepare_worker(session_id, url_or_path):
    with memory_lock:
        if session_id not in global_memory: return
        instance = global_memory[session_id]['instance']
        filepath = global_memory[session_id]['image_path']

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

            with memory_lock:
                global_memory[session_id]['image_path'] = filepath
        except Exception as e:
            print(f"Download failed for session {session_id}: {e}")
    
    if instance:
        try:
            instance.prepare(filepath)
        except Exception as e:
            print(f"Error during instance preparation: {e}")

@keymaker_bp.route('/api/process', methods=['POST'])
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

    thread = threading.Thread(target=download_and_prepare_worker, args=(session_id, url))
    thread.start()
    return jsonify({'status': 'processing'})

@keymaker_bp.route('/api/poll', methods=['GET'])
def poll_status():
    session_id = request.args.get('session_id')
    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    instance = global_memory[session_id]['instance']
    if not instance:
        return jsonify({'ready': False})
    return jsonify({'ready': instance.ready()})

@keymaker_bp.route('/api/image', methods=['GET'])
def get_image():
    session_id = request.args.get('session_id')
    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    filepath = global_memory[session_id].get('image_path')
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'Image not found'}), 404

    return send_file(filepath, mimetype='image/png')

@keymaker_bp.route('/api/mask', methods=['GET'])
def get_mask():
    session_id = request.args.get('session_id')
    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    instance = global_memory[session_id]['instance']
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

@keymaker_bp.route('/api/step4', methods=['POST'])
def step4_submit():
    data = request.json or {}
    session_id = data.get('session_id')
    labels = data.get('labels')

    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    touch_session(session_id)
    instance = global_memory[session_id]['instance']
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

    if not session_id or session_id not in global_memory:
        return jsonify({'error': 'Invalid session identifier'}), 400

    instance = global_memory[session_id]['instance']
    try:
        # Retrieve raw text from image game processing
        raw_result_string = instance.result()
        
        # Drop the raw tool output securely inside the backend memory footprint
        session['raw_tool_output'] = raw_result_string
        
        # Pop the tracking link pointing back to the core platform UI context
        target_callback = session.pop('tool_callback', '/')
        
        # Inform frontend SPA to break execution and return to the main application context
        return jsonify({
            'status': 'redirect',
            'redirect_url': target_callback
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500