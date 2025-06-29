from flask import Flask, request, jsonify, send_from_directory
import fitz  # PyMuPDF
import os
import google.generativeai as genai
import json
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy

load_dotenv()  # Load env vars from .env locally

app = Flask(__name__)
CORS(app)  # Enable CORS fully

# --- Database setup ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///policies.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class Policy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256), unique=True, nullable=False)
    text = db.Column(db.Text, nullable=False)

# --- Google GenAI setup ---
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise Exception("Missing GOOGLE_API_KEY environment variable!")
genai.configure(api_key=API_KEY)

# --- Uploads folder ---
UPLOAD_FOLDER = "./uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- In-memory cache of policy texts ---
policy_texts = []

# --- Serve index.html at root ---
@app.route("/")
def serve_index():
    return send_from_directory("static", "index.html")

# --- Utility: Extract text from PDF ---
def extract_text_from_pdf(file_obj):
    if isinstance(file_obj, str):
        doc = fitz.open(file_obj)
    else:
        file_obj.seek(0)
        doc = fitz.open(stream=file_obj.read(), filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    doc.close()
    return text

# --- Load all policies from DB into memory ---
def load_policies_on_startup():
    global policy_texts
    policy_texts = [p.text for p in Policy.query.all()]
    print(f"Loaded {len(policy_texts)} policy documents from database")

# --- Upload endpoint ---
@app.route('/upload_policy', methods=['POST'])
def upload_policy():
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    uploaded_count = 0
    for file in files:
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        text = extract_text_from_pdf(filepath)
        # Save to DB, update if filename exists
        policy = Policy.query.filter_by(filename=filename).first()
        if policy:
            policy.text = text
        else:
            policy = Policy(filename=filename, text=text)
            db.session.add(policy)
        uploaded_count += 1
    db.session.commit()

    load_policies_on_startup()  # Refresh in-memory cache

    return jsonify({"message": f"{uploaded_count} policy documents uploaded and processed."})

# --- Compliance check endpoint ---
@app.route('/check_document', methods=['POST', 'OPTIONS'])
def check_document():
    if request.method == 'OPTIONS':
        return '', 200

    if not policy_texts:
        return jsonify({"error": "No compliance policies uploaded. Please upload policies first."}), 400

    try:
        data = request.get_json()
        user_text = data.get("document_text")

        if not user_text:
            return jsonify({"error": "No document_text found in request."}), 400

        combined_policy = "\n\n".join(policy_texts)

        prompt = f"""
You are an expert compliance officer. Analyze the user document for any violations only against the provided policies.

Policies:
{combined_policy}

User Document:
{user_text}

Return your analysis as a single, valid JSON object. Do not include any text or formatting like "```json".
The JSON object must have a root key "compliance_report". The value should be an object with two keys:
- "is_compliant": a boolean value (true or false).
- "violations": an array of objects. Each object in the array should have:
  - "rule_violated": a string describing the rule that was violated.
  - "violating_text": a string containing the exact text from the document.
  - "suggestion": a string suggesting a fix.

If there are no violations, the "violations" array should be empty and "is_compliant" should be true.
"""

        model = genai.GenerativeModel("gemini-1.5-flash-latest")
        generation_config = genai.types.GenerationConfig(response_mime_type="application/json")

        response = model.generate_content(prompt, generation_config=generation_config)

        print("Raw response:", response.text)

        report_data = json.loads(response.text)
        return jsonify(report_data)

    except json.JSONDecodeError:
        return jsonify({"error": "Failed to decode JSON from AI response."}), 500

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": "Internal error during compliance check."}), 500

# --- Initialize DB and load policies on startup ---
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        load_policies_on_startup()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))