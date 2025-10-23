from flask import Flask, render_template, request, redirect, url_for, flash
from pymongo import MongoClient
from dotenv import load_dotenv
import os
import pdfplumber
import re
from pdf2image import convert_from_path
import pytesseract

# Load environment variables
load_dotenv()
MONGO_URI = os.getenv('MONGO_URI')

# Initialize MongoDB Client
mongo_client = None
mongo_db = None
try:
    mongo_client = MongoClient(MONGO_URI)
    # Choose a database (replace 'credit_card_parser' with your preferred name, or set in .env)
    mongo_db = mongo_client.get_database('credit_card_parser')
except Exception as e:
    print(f"Error connecting to MongoDB Atlas: {e}")
    mongo_db = None

app = Flask(__name__)
app.secret_key = 'supersecretkey'
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Parsing Helpers ---
def extract_text_from_pdf(filepath):
    text = ""
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
    except Exception as e:
        print(f"pdfplumber error: {e}")
    # If no text was extracted, try OCR via PyMuPDF
    if not text.strip():
        print("pdfplumber found no text. Trying OCR via PyMuPDF + pytesseract...")
        try:
            import fitz  # PyMuPDF
            import io
            from PIL import Image
            text_pages = []
            abs_path = os.path.abspath(filepath)
            pdf_doc = fitz.open(abs_path)
            for page_num in range(pdf_doc.page_count):
                page = pdf_doc.load_page(page_num)
                pix = page.get_pixmap()
                img_bytes = pix.pil_tobytes(format="png")
                img = Image.open(io.BytesIO(img_bytes))
                img.save(f'DEBUG_Page_{page_num+1}.png')
                print(f'Saved page image: DEBUG_Page_{page_num+1}.png')
                # Preprocess image for better OCR
                gray_img = img.convert('L')
                from PIL import ImageEnhance
                contrast = ImageEnhance.Contrast(gray_img).enhance(2.0)
                sharp = ImageEnhance.Sharpness(contrast).enhance(2.0)
                # Tesseract OCR on processed image
                page_ocr = pytesseract.image_to_string(sharp)
                text_pages.append(page_ocr)
            text = "\n".join(text_pages)
            print("Extracted OCR text:")
            print(text[:1000])
            with open('DEBUG_OCR_TEXT.txt', 'w', encoding='utf8') as f:
                f.write(text)
            print("PyMuPDF-based OCR text extraction completed.")
        except Exception as ocr_err:
            print(f"OCR extraction error (PyMuPDF): {ocr_err}")
            text = f"OCR error: {ocr_err}"
    return text

def detect_bank(text):
    if re.search(r'BUILDING BLOCKS STUDENT HANDOUT|Sample credit card statement', text, re.I):
        return 'SAMPLE'
    if re.search(r'HDFC.*Credit Card Statement', text, re.I):
        return 'HDFC'
    if re.search(r'HDFC', text, re.I):  # fallback
        return 'HDFC'
    if re.search(r'ICICI', text, re.I):
        return 'ICICI'
    elif re.search(r'State Bank of India|SBI', text, re.I):
        return 'SBI'
    elif re.search(r'Axis Bank|AXIS', text, re.I):
        return 'AXIS'
    elif re.search(r'American Express|Amex', text, re.I):
        return 'AMEX'
    else:
        return None

def parse_sample(text):
    name = re.search(r'Name:\s*(.+)', text)
    card_last4 = re.search(r'Account\s*Number:\s*[0-9\-]+([0-9]{4})', text)
    bill_period = re.search(r'Opening/Closing Date\s*([\d/\-]+ *[–-] *[\d/\-]+)', text)
    total_due = re.search(r'New Balance:\s*\$([\d,\.]+)', text)
    due_date = re.search(r'Payment Due Date:\s*([\d/]+)', text)
    fields = {
        'Bank': 'Sample',
        'Cardholder Name': (name.group(1) if name else 'N/A'),
        'Card Last 4 Digits': (card_last4.group(1) if card_last4 else 'N/A'),
        'Billing Period': (bill_period.group(1) if bill_period else 'N/A'),
        'Total Amount Due': (total_due.group(1) if total_due else 'N/A'),
        'Payment Due Date': (due_date.group(1) if due_date else 'N/A'),
    }
    return fields

def parse_hdfc(text):
    # Cardholder Name: attempt to find a likely name line (all-caps, 2+ tokens)
    name = re.search(r'\b([A-Z]{3,}(?: [A-Z]{2,}){1,2})\b', text)

    # Card Last 4 Digits: HDFC OCR may jumble, fallback to none
    card_last4 = re.search(r'(\d{4})\b(?![-\d])', text)

    # Billing Period: not clear in OCR, fallback to none
    bill_period = re.search(r'Billing Period[\s:]+([0-9/\- ]+[–-][0-9/\- ]+)', text, re.I)

    # Total Amount Due: Fuzzy match to TOTAL AMOUNT or AMOUNT DUE followed by a number (may be noisy)
    total_due = re.search(r'TOTAL AMOUNT[^\d]*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', text, re.I)
    if not total_due:
        total_due = re.search(r'AMOUNT DUE[^\d]*(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', text, re.I)
    
    # Payment Due Date: not reliably extractable, fallback to none
    due_date = re.search(r'Due Date\s*[:]?\s*([\d\-/]+)', text)

    fields = {
        'Bank': 'HDFC',
        'Cardholder Name': (name.group(1).title() if name else 'N/A'),
        'Card Last 4 Digits': (card_last4.group(1) if card_last4 else 'N/A'),
        'Billing Period': (bill_period.group(1) if bill_period else 'N/A'),
        'Total Amount Due': (total_due.group(1) if total_due else 'N/A'),
        'Payment Due Date': (due_date.group(1) if due_date else 'N/A'),
    }
    return fields

def parse_icici(text):
    # TODO
    return {'Bank': 'ICICI', 'Cardholder Name': 'N/A', 'Card Last 4 Digits': 'N/A', 'Billing Period': 'N/A','Total Amount Due': 'N/A','Payment Due Date': 'N/A'}

def parse_sbi(text):
    # TODO
    return {'Bank': 'SBI', 'Cardholder Name': 'N/A', 'Card Last 4 Digits': 'N/A', 'Billing Period': 'N/A','Total Amount Due': 'N/A','Payment Due Date': 'N/A'}

def parse_axis(text):
    # TODO
    return {'Bank': 'AXIS', 'Cardholder Name': 'N/A', 'Card Last 4 Digits': 'N/A', 'Billing Period': 'N/A','Total Amount Due': 'N/A','Payment Due Date': 'N/A'}

def parse_amex(text):
    # TODO
    return {'Bank': 'AMEX', 'Cardholder Name': 'N/A', 'Card Last 4 Digits': 'N/A', 'Billing Period': 'N/A','Total Amount Due': 'N/A','Payment Due Date': 'N/A'}

def extract_fields(text, bank):
    if bank == 'SAMPLE':
        return parse_sample(text)
    if bank == 'HDFC':
        return parse_hdfc(text)
    elif bank == 'ICICI':
        return parse_icici(text)
    elif bank == 'SBI':
        return parse_sbi(text)
    elif bank == 'AXIS':
        return parse_axis(text)
    elif bank == 'AMEX':
        return parse_amex(text)
    else:
        return None

@app.route('/result')
def result():
    result_data = request.args.get('data')
    if not result_data:
        return 'No data to show.', 400
    import json
    try:
        fields = json.loads(result_data)
    except Exception as e:
        return f'Error decoding data: {e}', 500
    return render_template('result.html', fields=fields)

# Modify upload_file to parse after upload
@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        if file:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)

            # --- Parse the PDF ---
            text = extract_text_from_pdf(filepath)
            print('-' * 60)
            print('Extracted Text Preview:')
            print(text[:1000])
            print('-' * 60)
            bank = detect_bank(text)
            if not bank:
                flash('Unsupported or unknown statement format. Preview of extracted text is printed in your console.')
                return redirect(request.url)
            fields = extract_fields(text, bank)

            # Pass data to /result
            import json
            return redirect(url_for('result', data=json.dumps(fields)))
    return render_template('upload.html')

if __name__ == '__main__':
    app.run(debug=True)
