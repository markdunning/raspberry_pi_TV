import os
import sys
import requests
import xml.etree.ElementTree as ET
import subprocess
import json

# Define supported video file extensions (must match main script)
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv")

# --- Helper for Local Files ---

def get_video_metadata(file_path):
    """Uses FFprobe to extract duration, width, and height from a local video file."""
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", file_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        metadata = json.loads(result.stdout)
        
        duration = metadata.get('format', {}).get('duration')
        video_stream = next((s for s in metadata.get('streams', []) if s.get('codec_type') == 'video'), None)
        
        if video_stream:
            width = video_stream.get('width')
            height = video_stream.get('height')
        else:
            width, height = None, None
            
        duration_str = str(round(float(duration), 2)) if duration else None

        if duration_str and width and height:
            return {
                "length": duration_str,
                "width": str(width),
                "height": str(height),
                "size": metadata.get('format', {}).get('size'),
                "format_name": metadata.get('format', {}).get('format_name'),
                "mtime": str(int(os.path.getmtime(file_path)))
            }
        
    except (subprocess.CalledProcessError, json.JSONDecodeError, StopIteration, ValueError, TypeError, FileNotFoundError) as e:
        print(f"Warning: Could not get metadata for {file_path}. Error: {e}")
        return None
    return None

# --- Main Unified Function ---

def generate_metadata_xml(input_target):
    """
    Detects if the input_target is a URL or a local folder and generates the 
    standardized transformed XML file accordingly.
    """
    
    if input_target.lower().startswith(('http://', 'https://')):
        # Input is a URL (Archive.org Base Download URL)
        return _process_archive_url(input_target)
    else:
        # Input is assumed to be a local folder path
        return _process_local_folder(input_target)

# --- Logic for Archive.org URL Input ---

def _process_archive_url(base_url):
    """Handles the transformation of an Archive.org XML file."""
    
    if not base_url.endswith('/'):
        base_url += '/'
        
    try:
        item_id = base_url.split('/')[-2]
    except IndexError:
        print(f"Error: Could not extract Item ID from Base URL: {base_url}")
        return False

    xml_url = f"{base_url}{item_id}_files.xml"
    print(f"Item ID determined: {item_id}")
    print(f"Fetching XML from: {xml_url}")

    try:
        response = requests.get(xml_url)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching XML URL {xml_url}: {e}")
        return False

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        print(f"Error parsing XML content: {e}")
        return False

    transformation_count = 0
    
    # Iterate over the children of the root element (which is <files>)
    # We iterate backwards over a copy of the list for safe removal of elements.
    for i in range(len(root) - 1, -1, -1):
        file_element = root[i] # Get the child element
        
        # Check if the element is an actual <file> tag (it should be, but is safer)
        if file_element.tag != 'file':
            continue

        filename = file_element.attrib.get('name')
        
        # Check if the file is a video
        if (filename is not None and 
            filename.lower().endswith(VIDEO_EXTENSIONS)):
            
            # --- VIDEO: Perform Transformation ---
            full_url = base_url + filename
            file_element.set('name', full_url)
            transformation_count += 1
        else:
            # --- NON-VIDEO: Remove Element Safely ---
            # Remove the element directly from the parent (root)
            root.remove(file_element)
            
    if transformation_count == 0:
        print("Warning: No video file entries were found to modify.")
        return False

    final_output_file = f"{item_id}_transformed.xml"
    try:
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ", level=0) 
        tree.write(final_output_file, encoding='utf-8', xml_declaration=True)
        print(f"\nSuccessfully created transformed XML file: {final_output_file}")
        return True
    except IOError as e:
        print(f"Error writing output file {final_output_file}: {e}")
        return False

# --- Logic for Local Folder Input ---

def _process_local_folder(folder_path):
    """Handles the generation of XML metadata for a local folder."""
    if not os.path.isdir(folder_path):
        print(f"Error: Folder path not found: {folder_path}")
        return False

    item_id = os.path.basename(os.path.normpath(folder_path))
    if not item_id:
        item_id = "local_videos"
        
    final_output_file = f"{item_id}_transformed.xml"
    root = ET.Element("files")
    
    print(f"Scanning local folder: {folder_path}")
    
    file_count = 0
    
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(VIDEO_EXTENSIONS):
            file_path = os.path.join(folder_path, filename)
            
            metadata = get_video_metadata(file_path)
            
            if metadata:
                file_count += 1
                
                # Create the <file> element
                file_element = ET.SubElement(root, "file")
                
                # Use the full local path as the 'name' attribute
                file_element.set('name', file_path)
                file_element.set('source', 'local')
                
                # Add metadata as child elements
                for key, value in metadata.items():
                    if key not in ['format_name', 'size']: # Exclude metadata not strictly needed
                        child = ET.SubElement(file_element, key)
                        child.text = value
                
                if 'format_name' in metadata:
                    format_el = ET.SubElement(file_element, 'format')
                    format_el.text = metadata['format_name']
                    
                print(f"  > Added: {filename}")
    
    if file_count == 0:
        print(f"Warning: Found no valid video files ({VIDEO_EXTENSIONS}) in {folder_path}.")
        return False
        
    # Write the Transformed XML file
    try:
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ", level=0) 
        tree.write(final_output_file, encoding='utf-8', xml_declaration=True)
        print(f"\nSuccessfully created local metadata XML file: {final_output_file}")
        return True
    except IOError as e:
        print(f"Error writing output file {final_output_file}: {e}")
        return False

# ----------------------------------------------------------------------

if __name__ == "__main__":
    
    if len(sys.argv) < 2:
        print("Error: Please provide a URL (for Archive.org) or a local folder path.")
        print(f"Usage: python {sys.argv[0]} <URL_OR_PATH>")
        print("\nExamples:")
        print("python meta_generator.py https://archive.org/download/dogtanian-and-the-three-muskehounds/")
        print("python meta_generator.py /home/pi/Videos/commercials_day")
        sys.exit(1)

    input_target = sys.argv[1]
    
    print("--- Unified Metadata XML Generator ---")
    
    generate_metadata_xml(input_target)