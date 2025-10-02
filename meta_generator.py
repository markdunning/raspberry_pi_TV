import os
import sys
import requests
import xml.etree.ElementTree as ET
import subprocess
import json
import urllib.parse
import re

# Define supported video file extensions
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ogv", ".webm", ".mpeg", ".mpg")

# --- Helper Functions ---

def get_video_metadata(file_path):
    """Uses FFprobe to extract duration, width, and height from a local video file."""
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json", 
            "-show_streams", "-show_format", file_path
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
            
        # Format duration to two decimal places
        duration_str = str(round(float(duration), 2)) if duration and float(duration) > 0 else None

        if duration_str and width and height:
            return {
                "length": duration_str,
                "width": str(width),
                "height": str(height),
                "mtime": str(int(os.path.getmtime(file_path)))
            }
        
    except (subprocess.CalledProcessError, json.JSONDecodeError, StopIteration, ValueError, TypeError, FileNotFoundError) as e:
        # print(f"Warning: Could not get metadata for {file_path}. Error: {e}") 
        pass
    return None

def expand_url_pattern(pattern_url):
    """
    Expands a URL pattern containing one or more [start-end] ranges 
    into a list of full URLs by generating all possible combinations.
    """
    
    # Regex to find ALL patterns: [START-END]
    range_matches = list(re.finditer(r'\[(\d+)-(\d+)\]', pattern_url))
    
    if not range_matches:
        return [pattern_url] 

    if len(range_matches) > 2:
        print("Warning: Only up to two range patterns ([S-E]) are supported per URL. Using the first two.")
        range_matches = range_matches[:2]
        
    replacement_options = []
    
    for match in range_matches:
        start_str, end_str = match.group(1), match.group(2)
        start, end = int(start_str), int(end_str)
        is_padded = len(start_str) == 2 
        
        if start > end:
            print(f"Warning: Invalid range specified ({start}-{end}). Skipping pattern.")
            continue

        replacements = []
        for i in range(start, end + 1):
            if is_padded:
                number_str = f"{i:02d}"
            else:
                number_str = str(i)
            replacements.append(number_str)
            
        replacement_options.append({
            'pattern': match.group(0),
            'replacements': replacements
        })
        
    if not replacement_options:
        return [pattern_url]
        
    expanded_urls = []
    
    # Start with the first replacement option
    option_1 = replacement_options[0]
    
    for rep_1 in option_1['replacements']:
        temp_url = pattern_url.replace(option_1['pattern'], rep_1, 1) # Only replace the first match
        
        if len(replacement_options) == 1:
            # Single loop
            expanded_urls.append(temp_url)
        else:
            # Nested loop: two options (e.g., Series and Episode)
            option_2 = replacement_options[1]
            for rep_2 in option_2['replacements']:
                # Replace the second pattern in the already-modified URL
                final_url = temp_url.replace(option_2['pattern'], rep_2, 1) # Only replace the second match
                expanded_urls.append(final_url)

    if expanded_urls:
        print(f"Expanded pattern to {len(expanded_urls)} URLs.")
    else:
        print(f"Warning: Pattern expansion failed for {pattern_url}. Check format.")
        
    return expanded_urls

def combine_xml_results(results_list):
    """Aggregates a list of successful root elements into one final XML tree."""
    final_root = ET.Element("files")
    
    for root_element in results_list:
        if root_element is not None:
            # Transfer all children (<file> elements) to the final root
            for file_element in list(root_element):
                final_root.append(file_element)
    
    return final_root

# --- Processor Functions ---

def _process_archive_url(base_url, write_output=False):
    """Handles the transformation of an Archive.org XML file."""
    
    # 1. Clean the URL and extract the Item ID (robust for /download/ or /details/)
    parsed_url = urllib.parse.urlparse(base_url)
    path_segments = [segment for segment in parsed_url.path.strip('/').split('/') if segment]
    
    if not path_segments:
        print("  > Error: Could not find any path segments in the URL.")
        return None
        
    item_id = path_segments[-1]
    
    # 2. Reconstruct the clean base_url using the standard /download/ format
    base_url_prefix = f"{parsed_url.scheme}://{parsed_url.netloc}"
    base_url = f"{base_url_prefix}/download/{item_id}/"

    # 3. Construct the XML URL
    xml_url = f"{base_url}{item_id}_files.xml"
    
    print(f"  > Item ID: {item_id}. Fetching XML from: {xml_url}")

    # 4. Fetch and Parse the XML
    try:
        response = requests.get(xml_url)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        
    except requests.exceptions.HTTPError as e:
        print(f"  > Error: Could not fetch XML. Server returned {response.status_code}.")
        return None
    except requests.RequestException as e:
        print(f"  > Fatal Network Error: {e}")
        return None
    except ET.ParseError as e:
        print(f"  > Error parsing XML content: {e}")
        return None
        
    # ------------------------------------------------------------------
    # 5. Transform and Filter the XML 
    # ------------------------------------------------------------------
    transformation_count = 0
    
    if not list(root):
        print("  > Warning: The fetched XML file is empty or contains no elements.")
        return None
        
    # Iterate backwards for safe removal
    for i in range(len(root) - 1, -1, -1):
        file_element = root[i] 
        
        if file_element.tag != 'file':
            continue

        filename = file_element.attrib.get('name')
        
        if (filename is not None and 
            filename.lower().endswith(VIDEO_EXTENSIONS)):
            
            # --- VIDEO: Perform Transformation ---
            full_url = base_url + filename
            file_element.set('name', full_url)
            file_element.set('source', 'archive') # Add source flag
            transformation_count += 1
        else:
            # --- NON-VIDEO: Remove Element Safely ---
            root.remove(file_element)
            
    if transformation_count == 0:
        print("  > Warning: No video file entries found for transformation.")
        return None
    
    print(f"  > Success: Found and transformed {transformation_count} video entries.")

    # 6. Return root for aggregation or write if single file is requested
    if write_output:
        final_output_file = f"{item_id}_transformed.xml"
        try:
            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ", level=0) 
            tree.write(final_output_file, encoding='utf-8', xml_declaration=True)
            print(f"\nSUCCESS: Created single file: {final_output_file}")
        except IOError as e:
            print(f"Error writing output file {final_output_file}: {e}")
        return None
        
    return root

def _process_local_folder(folder_path, write_output=False):
    """Handles the generation of XML metadata for a local folder."""
    if not os.path.isdir(folder_path):
        print(f"  > Error: Folder path not found: {folder_path}")
        return None

    item_id = os.path.basename(os.path.normpath(folder_path))
    if not item_id:
        item_id = "local_videos"
        
    root = ET.Element("files")
    file_count = 0
    
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(VIDEO_EXTENSIONS):
            file_path = os.path.join(folder_path, filename)
            
            metadata = get_video_metadata(file_path)
            
            if metadata:
                file_count += 1
                
                # Create the <file> element
                file_element = ET.SubElement(root, "file")
                file_element.set('name', file_path) # Full local path as the 'name' attribute
                file_element.set('source', 'local')
                
                # Add metadata as child elements
                for key, value in metadata.items():
                    child = ET.SubElement(file_element, key)
                    child.text = value
                    
    if file_count == 0:
        print(f"  > Warning: Found no valid video files in {folder_path}.")
        return None
    
    print(f"  > Success: Generated metadata for {file_count} local files.")

    # Return root for aggregation or write if single file is requested
    if write_output:
        final_output_file = f"{item_id}_transformed.xml"
        try:
            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ", level=0) 
            tree.write(final_output_file, encoding='utf-8', xml_declaration=True)
            print(f"\nSUCCESS: Created single file: {final_output_file}")
        except IOError as e:
            print(f"Error writing output file {final_output_file}: {e}")
        return None

    return root

# ----------------------------------------------------------------------
# Main Execution Block
# ----------------------------------------------------------------------

if __name__ == "__main__":
    
    # Check for the --single flag
    is_single_mode = '--single' in sys.argv or '-s' in sys.argv
    
    if len(sys.argv) < 2 or (is_single_mode and len(sys.argv) < 3):
        print("Error: Please provide one or more targets (URL pattern, URL, or local folder).")
        print(f"Usage for Batch: python {sys.argv[0]} <TARGET_1> [TARGET_2] ...")
        print(f"Usage for Single: python {sys.argv[0]} --single <SINGLE_TARGET>") 
        print("\nBatch Examples:")
        print("1. Archive Show: python meta_generator.py 'https://archive.org/details/show_se[1-3]ep[01-10]'")
        print("2. Mixed Batch: python meta_generator.py '.../show_se[1-1]ep[01-02]' /home/pi/ads/day")
        sys.exit(1)

    # --- Single File Mode Logic ---
    if is_single_mode:
        try:
            # Find the index of the flag and get the target immediately after it
            target_index = sys.argv.index('--single') if '--single' in sys.argv else sys.argv.index('-s')
            target = sys.argv[target_index + 1]
        except (ValueError, IndexError):
            print("Error: The --single flag must be followed by exactly one target.")
            sys.exit(1)
            
        print("--- Unified Metadata XML Generator (Single File Mode) ---")
        
        # Dispatch to the correct processor, forcing write_output=True
        if target.lower().startswith(('http://', 'https://')):
            _process_archive_url(target, write_output=True)
        else:
            _process_local_folder(target, write_output=True)
            
    # --- Batch Mode Logic ---
    else:
        input_targets_patterns = sys.argv[1:] 
        all_targets = []
        all_results = []
        
        print("--- Unified Metadata XML Generator (Batch Mode) ---")
        
        # 1. Expand all patterns into a single list of actual URLs/paths
        for target in input_targets_patterns:
            if target.lower().startswith(('http://', 'https://')) and '[' in target:
                all_targets.extend(expand_url_pattern(target))
            else:
                all_targets.append(target)
                
        # 2. Process each target and collect results (write_output=False)
        for target in all_targets:
            print(f"\nProcessing target: {target}")
            
            if target.lower().startswith(('http://', 'https://')):
                root_element = _process_archive_url(target, write_output=False)
            else:
                root_element = _process_local_folder(target, write_output=False)
                
            if root_element is not None:
                all_results.append(root_element)

        if not all_results:
            print("\nFAILURE: No valid metadata could be generated from any of the inputs.")
            sys.exit(1)
            
        # 3. Combine and write the final unified XML file
        final_root = combine_xml_results(all_results)
        final_output_file = "combined_metadata_transformed.xml"
        
        try:
            tree = ET.ElementTree(final_root)
            ET.indent(tree, space="  ", level=0) 
            tree.write(final_output_file, encoding='utf-8', xml_declaration=True)
            print(f"\n=======================================================")
            print(f"SUCCESS: Created unified metadata XML file: {final_output_file}")
            print(f"Total video files aggregated: {len(final_root)}")
            print(f"=======================================================")
        except IOError as e:
            print(f"Error writing unified output file {final_output_file}: {e}")