from flask import Flask, request, jsonify, render_template
import fitz  # PyMuPDF
import os
import google.generativeai as genai
import json
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy

# Load environment variables from .env file (for local development)
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Enable CORS for all origins. This is useful during development if frontend and backend
# are running on different ports/domains. When served from the same Flask app, it's less critical
# but harmless to keep.
CORS(app)

# --- Database Configuration ---
# Use SQLite for simplicity.
# IMPORTANT: On Render's free tier, local SQLite files are ephemeral and will be lost
# upon container restart. For persistent data on Render, consider using their
# managed PostgreSQL service and update this line:
# app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///policies.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Disable tracking modifications
db = SQLAlchemy(app)

# --- Database Model for Policies ---
class Policy(db.Model):
    """
    SQLAlchemy model for storing compliance policies.
    Each policy stores its filename and the extracted text content.
    """
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    content = db.Column(db.Text, nullable=False) # Stores the extracted text from the PDF

    def __repr__(self):
        return f'<Policy {self.filename}>'

# Create database tables if they don't exist.
# This must be done within the Flask application context.
with app.app_context():
    db.create_all()

# --- Gemini API Configuration ---
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise Exception("Missing GOOGLE_API_KEY environment variable! Please set it in .env or your environment.")
genai.configure(api_key=API_KEY)

# --- Helper Function for PDF Text Extraction ---
def extract_text_from_pdf(file_obj):
    """
    Extracts text content from a PDF file object.
    Args:
        file_obj: A file-like object (e.g., from request.files).
    Returns:
        A string containing all extracted text.
    """
    file_obj.seek(0) # Ensure the file pointer is at the beginning
    doc = fitz.open(stream=file_obj.read(), filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    doc.close()
    return text

# --- Flask Routes (API Endpoints & UI Serving) ---

@app.route('/')
def serve_index():
    """
    Serves the main HTML page (index.html) for the application.
    Flask automatically looks for templates in the 'templates' folder.
    """
    return render_template('index.html')

@app.route('/upload_policy', methods=['POST'])
def upload_policy():
    """
    Handles the upload of PDF policy documents.
    Extracts text from PDFs and stores it in the database.
    """
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    uploaded_count = 0
    errors = []

    for file in files:
        filename = secure_filename(file.filename)
        # Check if policy already exists to prevent duplicates
        existing_policy = Policy.query.filter_by(filename=filename).first()
        if existing_policy:
            errors.append(f"Policy '{filename}' already exists. Skipping upload for this file.")
            continue # Skip to next file

        try:
            extracted_text = extract_text_from_pdf(file)
            new_policy = Policy(filename=filename, content=extracted_text)
            db.session.add(new_policy)
            db.session.commit()
            uploaded_count += 1
        except Exception as e:
            db.session.rollback() # Rollback changes if an error occurs
            errors.append(f"Error processing {filename}: {str(e)}")

    response_message = f"{uploaded_count} policy documents uploaded and stored."
    if errors:
        response_message += " Some files encountered errors."
        return jsonify({"message": response_message, "errors": errors}), 200
    return jsonify({"message": response_message}), 200

@app.route('/get_policies', methods=['GET'])
def get_policies():
    """
    Retrieves a list of all uploaded policies from the database.
    Returns:
        JSON array of policy objects (id, filename).
    """
    policies = Policy.query.all()
    policy_list = [{"id": p.id, "filename": p.filename} for p in policies]
    return jsonify(policy_list)

@app.route('/delete_policy/<int:policy_id>', methods=['DELETE'])
def delete_policy(policy_id):
    """
    Deletes a specific policy from the database by its ID.
    """
    policy = Policy.query.get(policy_id)
    if not policy:
        return jsonify({"error": "Policy not found"}), 404
    try:
        db.session.delete(policy)
        db.session.commit()
        return jsonify({"message": f"Policy '{policy.filename}' deleted successfully."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete policy: {str(e)}"}), 500

@app.route('/check_document', methods=['POST', 'OPTIONS'])
def check_document():
    """
    Analyzes a user-provided document text against all stored policies
    using the Gemini 1.5 Flash model.
    Returns:
        A JSON compliance report.
    """
    if request.method == 'OPTIONS':
        # Handle CORS preflight request
        return '', 200

    # Retrieve all policy texts from the database
    all_policies = Policy.query.all()
    if not all_policies:
        return jsonify({"error": "No compliance policies uploaded. Please upload policies first."}), 400

    # Combine all policy texts into a single string for the LLM prompt
    combined_policy = "\n\n".join([p.content for p in all_policies])

    try:
        data = request.get_json()
        user_text = data.get("document_text")

        if not user_text:
            return jsonify({"error": "No document_text found in request."}), 400

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
        # Ensure the model generates a JSON response
        generation_config = genai.types.GenerationConfig(response_mime_type="application/json")

        response = model.generate_content(prompt, generation_config=generation_config)

        # For debugging: print the raw response text from the LLM
        print("Raw LLM response:", response.text)

        # Parse the JSON response from the LLM
        report_data = json.loads(response.text)
        return jsonify(report_data)

    except json.JSONDecodeError:
        # Catch errors if the LLM response is not valid JSON
        return jsonify({"error": "Failed to decode JSON from AI response. LLM might have returned malformed JSON."}), 500
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred during compliance check: {e}")
        return jsonify({"error": "Internal error during compliance check."}), 500

# --- Main entry point for running the Flask app ---
if __name__ == "__main__":
    # When running locally, host="0.0.0.0" makes it accessible from other devices on the network.
    # The port is taken from environment variables (e.g., set by Render) or defaults to 5000.
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

