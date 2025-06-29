import os
import sqlite3
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from pdfminer.high_level import extract_text

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_PATH = 'policies.db'

# Initialize DB (create table if not exists)
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE,
                text TEXT
            )
        ''')

init_db()

policy_texts = []

def load_policy_texts():
    global policy_texts
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT filename, text FROM policies")
        policy_texts = [{"filename": r[0], "text": r[1]} for r in cur.fetchall()]

load_policy_texts()

def extract_text_from_pdf(filepath):
    return extract_text(filepath)

@app.route('/upload_policy', methods=['POST'])
def upload_policy():
    if 'files' not in request.files:
        return jsonify({"error": "No file part"}), 400

    files = request.files.getlist('files')
    saved_count = 0

    for file in files:
        if file.filename == '':
            continue

        filename = secure_filename(file.filename)
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(save_path)

        try:
            text = extract_text_from_pdf(save_path)
        except Exception as e:
            return jsonify({"error": f"Failed to extract text: {e}"}), 500

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO policies (filename, text) VALUES (?, ?)
            ''', (filename, text))
        saved_count += 1

    load_policy_texts()
    return jsonify({"message": f"{saved_count} policy document(s) uploaded and processed."})

@app.route('/check_document', methods=['POST'])
def check_document():
    data = request.get_json()
    if not data or 'document_text' not in data:
        return jsonify({"error": "Missing 'document_text' in JSON body"}), 400

    doc_text = data['document_text'].lower()

    violations = []
    for policy in policy_texts:
        if policy["text"].lower() not in doc_text:
            violations.append({
                "rule_violated": f"Missing policy from {policy['filename']}",
                "violating_text": "(text not found in document)",
                "suggestion": "Include the policy text to comply."
            })

    return jsonify({
        "compliance_report": {
            "is_compliant": len(violations) == 0,
            "violations": violations
        }
    })

# Serve frontend index.html
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

# Serve other static files if needed (css/js/images)
@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
