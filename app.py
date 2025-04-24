from flask import Flask, render_template, request, send_file, jsonify, url_for
from PyPDF2 import PdfReader, PdfWriter
import os
from datetime import datetime
from flask_cors import CORS
import xml.etree.ElementTree as ET
from defusedxml.ElementTree import fromstring
import re

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configure maximum file size (e.g., 16MB)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Configure upload folder
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def parse_xmp_metadata(xmp_string):
    if not xmp_string:
        return {}
    
    try:
        # Remove XML declaration if present
        xmp_string = re.sub(r'<\?xml[^>]+\?>', '', xmp_string)
        
        # Parse XMP data
        root = fromstring(xmp_string)
        
        # Initialize dictionary for XMP data
        xmp_data = {}
        
        # Extract all namespaces
        namespaces = dict([node for _, node in ET.iterparse(
            ET.StringIO(xmp_string), events=['start-ns'])])
        
        # Function to clean namespace prefix from tag
        def clean_tag(tag):
            return tag.split('}')[-1] if '}' in tag else tag

        # Recursive function to extract all elements
        def extract_elements(element, data_dict):
            for child in element:
                tag = clean_tag(child.tag)
                if len(child) > 0:
                    data_dict[tag] = {}
                    extract_elements(child, data_dict[tag])
                else:
                    if child.text and child.text.strip():
                        data_dict[tag] = child.text.strip()
                
                # Get attributes
                for key, value in child.attrib.items():
                    attr_tag = clean_tag(key)
                    if value and value.strip():
                        data_dict[f"{tag}_{attr_tag}"] = value.strip()

        # Extract all data
        extract_elements(root, xmp_data)
        return xmp_data
        
    except Exception as e:
        print(f"Error parsing XMP: {str(e)}")
        return {"Error": "Failed to parse XMP metadata"}

def extract_detailed_metadata(reader):
    metadata = {
        "Basic Metadata": {},
        "Document Information": {},
        "Page Information": {},
        "Security": {},
        "XMP Metadata": {},
        "Advanced Properties": {}
    }
    
    # Basic metadata
    if reader.metadata:
        for key, value in reader.metadata.items():
            clean_key = key.strip('/')
            metadata["Basic Metadata"][clean_key] = str(value)
    
    # Document information
    metadata["Document Information"]["Number of Pages"] = len(reader.pages)
    metadata["Document Information"]["PDF Version"] = reader.pdf_header if hasattr(reader, 'pdf_header') else 'Unknown'
    
    try:
        file_size = os.path.getsize(reader.stream.name)
        metadata["Document Information"]["File Size"] = f"{file_size / 1024:.2f} KB"
    except:
        metadata["Document Information"]["File Size"] = "Unknown"
    
    # Page information
    if len(reader.pages) > 0:
        first_page = reader.pages[0]
        page_info = metadata["Page Information"]
        
        # Media Box
        if '/MediaBox' in first_page:
            mediabox = first_page['/MediaBox']
            width = float(mediabox[2] - mediabox[0])
            height = float(mediabox[3] - mediabox[1])
            page_info["Page Size"] = f"{width:.2f} x {height:.2f} points"
        
        # Crop Box
        if '/CropBox' in first_page:
            cropbox = first_page['/CropBox']
            page_info["Crop Box"] = f"{cropbox}"
        
        # Rotation
        if '/Rotate' in first_page:
            page_info["Rotation"] = f"{first_page['/Rotate']} degrees"
        
        # Resources
        if '/Resources' in first_page:
            resources = first_page['/Resources']
            
            # Fonts
            if '/Font' in resources:
                fonts = resources['/Font']
                font_info = []
                for font in fonts.values():
                    if hasattr(font, '/BaseFont'):
                        font_info.append(str(font['/BaseFont']))
                if font_info:
                    page_info["Fonts"] = ', '.join(font_info)
            
            # XObjects
            if '/XObject' in resources:
                xobjects = resources['/XObject']
                page_info["XObject Count"] = len(xobjects)
    
    # Security information
    security = metadata["Security"]
    security["Is Encrypted"] = 'Yes' if reader.is_encrypted else 'No'
    
    if reader.is_encrypted:
        permissions = []
        if reader.can_print():
            permissions.append('Printing allowed')
        if reader.can_modify():
            permissions.append('Modification allowed')
        if reader.can_copy():
            permissions.append('Copying allowed')
        if reader.can_annotate():
            permissions.append('Annotation allowed')
        if permissions:
            security["Permissions"] = permissions
    
    # XMP Metadata
    try:
        xmp_data = reader.xmp_metadata
        if xmp_data:
            parsed_xmp = parse_xmp_metadata(xmp_data)
            metadata["XMP Metadata"] = parsed_xmp
    except Exception as e:
        metadata["XMP Metadata"]["Error"] = str(e)
    
    # Advanced properties
    advanced = metadata["Advanced Properties"]
    
    # Document catalog
    try:
        catalog = reader.trailer['/Root']
        if '/PageLayout' in catalog:
            advanced["Page Layout"] = str(catalog['/PageLayout'])
        if '/PageMode' in catalog:
            advanced["Page Mode"] = str(catalog['/PageMode'])
        if '/ViewerPreferences' in catalog:
            advanced["Has Viewer Preferences"] = "Yes"
    except:
        pass
    
    # Remove empty categories
    metadata = {k: v for k, v in metadata.items() if v}
    
    return metadata

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/analyze_pdf', methods=['POST'])
def analyze_pdf():
    if 'pdf_file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    try:
        # Read PDF and extract metadata
        reader = PdfReader(file)
        metadata = extract_detailed_metadata(reader)
        
        # Store the file temporarily for later processing
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        temp_path = os.path.join(UPLOAD_FOLDER, f'temp_{timestamp}.pdf')
        file.seek(0)  # Reset file pointer
        file.save(temp_path)

        return jsonify({
            'metadata': metadata,
            'temp_file': f'temp_{timestamp}.pdf'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/process_pdf/<temp_file>', methods=['GET'])
def process_pdf(temp_file):
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_file)
    
    if not os.path.exists(input_path):
        return jsonify({'error': 'Temporary file not found'}), 404

    try:
        # Process PDF
        reader = PdfReader(input_path)
        writer = PdfWriter()

        # Copy all pages
        for page in reader.pages:
            writer.add_page(page)

        # Copy metadata but KEEP ModDate and CreationDate
        metadata = reader.metadata
        cleaned_metadata = {}
        
        if metadata:
            for key, value in metadata.items():
                # Keep ModDate, CreationDate and other essential metadata
                if (key.lower() in ['/moddate', '/modified', '/lastmodified', '/creationdate', '/created'] or 
                    key.lower() == '/producer'):
                    cleaned_metadata[key] = value
                # You can add other metadata fields you want to keep here

        # Add cleaned metadata
        writer.add_metadata(cleaned_metadata)

        # Save processed file
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], f'processed_{temp_file}')
        with open(output_path, 'wb') as output_file:
            writer.write(output_file)

        # Clean up temporary input file
        if os.path.exists(input_path):
            os.remove(input_path)

        # Send the file
        return send_file(
            output_path,
            as_attachment=True,
            download_name=f'processed_{os.path.basename(temp_file)}',
            mimetype='application/pdf'
        )

    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 