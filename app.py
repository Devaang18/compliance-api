from flask import Flask, request, jsonify
import fitz  # PyMuPDF
import os
import google.generativeai as genai
import json
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()  # Load env vars from .env locally

app = Flask(__name__)
CORS(app)  # Enable CORS fully

API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise Exception("Missing GOOGLE_API_KEY environment variable!")
genai.configure(api_key=API_KEY)

UPLOAD_FOLDER = "./uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

policy_texts = []

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

def load_policies_on_startup():
    global policy_texts
    policy_texts = []
    for filename in os.listdir(UPLOAD_FOLDER):
        if filename.lower().endswith(".pdf"):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            text = extract_text_from_pdf(filepath)
            policy_texts.append(text)
    print(f"Loaded {len(policy_texts)} policy documents from {UPLOAD_FOLDER}")

@app.route('/upload_policy', methods=['POST'])
def upload_policy():
    global policy_texts

    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    for file in files:
        filename = secure_filename(file.filename)  # sanitize filename
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

    load_policies_on_startup()

    return jsonify({"message": f"{len(files)} policy documents uploaded and processed."})

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

if __name__ == "__main__":
    load_policies_on_startup()
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
