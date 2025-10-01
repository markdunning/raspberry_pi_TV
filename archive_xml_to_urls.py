import requests
import xml.etree.ElementTree as ET
import os
import sys
import re # Added for robust URL parsing

def create_urls_from_archive_xml(xml_url, output_filename):
    """
    Fetches an XML file from Archive.org, parses it to find video files,
    and writes the resulting full URLs to an output file.
    
    Returns the extracted item_id on success, or None on failure.
    """
    print(f"Fetching XML from: {xml_url}")

    try:
        # 1. Fetch the XML content
        response = requests.get(xml_url)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
    except requests.RequestException as e:
        print(f"Error fetching URL: {e}")
        return None

    try:
        # 2. Parse the XML content
        # Note: XML from '..._files.xml' often has the <files> root.
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        print(f"Error parsing XML content: {e}")
        return None

    # 3. Identify Item Identifier and Construct Base URL
    # Extract the identifier directly from the URL.
    try:
        # Assumes the identifier is the segment immediately after '/items/'
        match = re.search(r'/items/([^/]+)/', xml_url)
        if match:
            item_id = match.group(1)
        else:
            raise ValueError("Could not extract item ID from URL format.")

        # Construct the base download URL
        base_url = f"https://archive.org/download/{item_id}/"
        print(f"Archive Item ID extracted from URL: {item_id}")
        print(f"Base URL for files: {base_url}")
        
    except (AttributeError, ValueError) as e:
        print(f"Error processing XML URL to find item identifier: {e}")
        print("Please ensure the URL contains the pattern '/items/ITEM_ID/'")
        return None
    
    # 4. Extract Video Files and Build URL List
    VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv")
    url_list = []

    # Iterate through all `<file>` tags (the root of the XML)
    for file_element in root.findall(".//file"):
        # The filename is an ATTRIBUTE of the <file> tag (name="...").
        filename = file_element.attrib.get('name')
        
        if (filename is not None and 
            filename.lower().endswith(VIDEO_EXTENSIONS)):
            
            # Construct the full URL (Base URL + filename)
            full_url = base_url + filename
            url_list.append(full_url)
            
    if not url_list:
        print("Warning: No video files found with the specified extensions.")
        return None
        
    print(f"Found {len(url_list)} video files.")

    # 5. & 6. Write the .urls file
    try:
        # Write to the temporary filename provided by the caller
        with open(output_filename, 'w') as f:
            for url in url_list:
                f.write(url + "\n")
        # Return the item_id so the caller can rename the file
        return item_id 
    except IOError as e:
        print(f"Error writing output file {output_filename}: {e}")
        return None

# ----------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Error: Please provide the Archive.org XML URL as a command-line argument.")
        print(f"Usage: python {sys.argv[0]} <XML_URL>")
        print("Example: python archive_to_urls.py https://.../dogtanian-and-the-three-muskehounds/dogtanian-and-the-three-muskehounds_files.xml")
        sys.exit(1)

    xml_url = sys.argv[1]
    
    # Use a temporary name first to ensure the full list is generated
    temp_output_file = "temp_playlist_output.urls"
    
    print("--- Archive.org URL List Builder ---")
    
    # Call the function and get the item ID back
    item_id = create_urls_from_archive_xml(xml_url, temp_output_file)

    if item_id:
        # 1. Determine the final, dynamic filename
        final_output_file = f"{item_id}.urls"