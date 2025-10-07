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

# --- PRUNING CONFIGURATION AND HELPERS ---

# Preferred format order (Lowest index = Highest Priority)
FORMAT_PRIORITY = [
    '.mp4',    # Score 0: Highest priority (Standard MP4)
    '.m4v',    # Score 1
    '.mov',    # Score 2
    '.webm',   # Score 3
    '.ogv',    # Score 4
    '.mkv',    # Score 5
    '.avi',    # Score 6
    '.ia.mp4', # Score 7 (Internet Archive derivative - lower priority than standard .mp4)
]

PRIORITY_MAP = {ext: i for i, ext in enumerate(FORMAT_PRIORITY)}

def get_file_extension(filename):
    """Safely extracts the file extension from a path, prioritizing multi-part extensions."""
    for ext in FORMAT_PRIORITY:
        if filename.lower().endswith(ext):
            return ext
            
    # Fallback for unexpected extensions
    return os.path.splitext(os.path.basename(filename))[1].lower()

def extract_base_filename(url_or_path):
    """Strips the path and the file extension(s) to get the base episode identifier for grouping."""
    base_name_with_ext = os.path.basename(url_or_path)
    
    # Sort extensions by length descending to strip multi-part extensions like '.ia.mp4' first
    stripped_name = base_name_with_ext
    for ext in sorted(FORMAT_PRIORITY, key=len, reverse=True):
        if stripped_name.lower().endswith(ext):
            # Strip the matching extension
            stripped_name = stripped_name[:-len(ext)]
            break 
    
    return stripped_name.strip()

def prune_xml_data(root):
    """
    Takes an XML root element, prunes duplicates based on format priority 
    using the base filename as the unique key, and returns the new root.
    """
    unique_episodes = {}
    
    # 1. Iterate and Select the Preferred Variant
    for file_tag in root.findall('.//file'): 
        file_name = file_tag.get('name')
        
        # --- Identify Unique Key: ALWAYS use the base filename without extension ---
        unique_key = extract_base_filename(file_name) 

        if not unique_key:
            continue
        
        # Get the format extension and its priority score
        file_ext = get_file_extension(file_name)
        priority_score = PRIORITY_MAP.get(file_ext, len(FORMAT_PRIORITY) + 1)
        
        if unique_key not in unique_episodes:
            # First time seeing this episode
            unique_episodes[unique_key] = (priority_score, file_tag)
            
        else:
            # Duplicate found!
            current_best_score, _ = unique_episodes[unique_key]
            
            # Compare scores (lower score is better/higher priority)
            if priority_score < current_best_score:
                # New candidate is better! Replace the stored element.
                unique_episodes[unique_key] = (priority_score, file_tag)
                
    # 2. Build the New XML Tree from the kept elements
    new_root = ET.Element(root.tag, root.attrib)
    
    # Sort by key for deterministic output order (and easier checking)
    sorted_episodes = sorted(unique_episodes.items())
    
    for _, (_, element) in sorted_episodes:
        new_root.append(element)
        
    # Return the root of the pruned XML
    return new_root
    
# --- Helper Functions (Existing) ---

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

def combine_xml_results(results_list, raw_results_list=None):
    """
    Aggregates a list of successful root elements into one final XML tree. 
    It saves the unpruned version if requested and then prunes the main version.
    """
    
    # 1. Aggregate all files into one raw root
    raw_root = ET.Element("files")
    
    # Use a separate root for the UNPRUNED version to ensure isolation
    original_root_to_save = ET.Element("files") if raw_results_list is not None else None

    for root_element in results_list:
        if root_element is not None:
            # Transfer all children (<file> elements) to the final root
            for file_element in list(root_element):
                
                # --- FIX: Deep copy the element for the unpruned list ---
                if original_root_to_save is not None:
                    # Create a deep copy of the element *before* it's added to the main raw_root
                    # which will be modified by the pruning step.
                    raw_element_copy = ET.fromstring(ET.tostring(file_element, encoding='unicode'))
                    original_root_to_save.append(raw_element_copy)
                
                # Append the main element to the raw_root for the pruning process
                raw_root.append(file_element)
    
    # 2. Save the RAW, combined root if a list was passed
    if original_root_to_save is not None:
        # The original_root_to_save is already populated with deep copies
        raw_results_list.append(original_root_to_save)


    # 3. Prune the combined results
    if raw_root:
        # The pruning operation will remove elements from raw_root.
        final_root = prune_xml_data(raw_root) 
    else:
        final_root = raw_root # Empty root

    return final_root

# --- Processor Functions ---

def _process_archive_url(base_url, write_output=False, keep_original=False):
    """
    Handles the transformation of an Archive.org XML file, including PRUNING and 
    optional saving of the original XML.
    """
    
    # 1. Clean the URL and extract the Item ID (robust for /download/ or /details/)
    parsed_url = urllib.parse.urlparse(base_url)
    path_segments = [segment for segment in parsed_url.path.strip('/').split('/') if segment]
    
    if not path_segments:
        print("  > Error: Could not find any path segments in the URL.")
        return None
        
    item_id = path_segments[-1]
    
    # 2. Reconstruct the clean base_url using the standard /download/ format
    base_url_prefix = f"{parsed_url.scheme}://{parsed_url.netloc}"
    base_url = f"{base_url_prefix}/download/{item_id}/"

    # 3. Construct the XML URL
    xml_url = f"{base_url}{item_id}_files.xml"
    
    print(f"  > Item ID: {item_id}. Fetching XML from: {xml_url}")

    # 4. Fetch and Parse the XML
    try:
        response = requests.get(xml_url)
        response.raise_for_status()
        
        # Parse the content into an ElementTree for processing
        root = ET.fromstring(response.content)

        # --- Save the original raw XML if requested (Single Mode) ---
        if keep_original and write_output: 
            original_output_file = f"{item_id}_original.xml"
            try:
                # Use ElementTree for consistent formatting
                original_root_for_save = ET.fromstring(response.content)
                original_tree = ET.ElementTree(original_root_for_save)
                ET.indent(original_tree, space="  ", level=0) 
                original_tree.write(original_output_file, encoding='utf-8', xml_declaration=True)
                print(f" 💾 Saved original XML as: {original_output_file}")
            except (IOError, ET.ParseError) as e:
                print(f" ❌ Error writing original XML file: {e}")
        
    except requests.exceptions.HTTPError as e:
        print(f"  > Error: Could not fetch XML. Server returned {response.status_code}.")
        return None
    except requests.RequestException as e:
        print(f"  > Fatal Network Error: {e}")
        return None
    except ET.ParseError as e:
        print(f"  > Error parsing XML content: {e}")
        return None
        
    # ------------------------------------------------------------------
    # 5. Transform and Filter the XML (Convert local paths to full URLs, filter non-videos)
    # ------------------------------------------------------------------
    transformation_count = 0
    
    if not list(root):
        print("  > Warning: The fetched XML file is empty or contains no elements.")
        return None
        
    # Use a separate list to hold the transformed elements to return for batch aggregation
    transformed_elements = []

    # Iterate backwards for safe removal/filtering logic
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
            
            transformed_elements.append(file_element)
            
    if transformation_count == 0:
        print("  > Warning: No video file entries found for transformation.")
        return None
    
    # Create a new root from the transformed elements to pass to pruning/aggregation
    transformed_unpruned_root = ET.Element(root.tag, root.attrib)
    for element in transformed_elements:
        transformed_unpruned_root.append(element)

    # ------------------------------------------------------------------
    # 6. Prune Duplicates (Operate on the transformed_unpruned_root)
    # ------------------------------------------------------------------
    # Prune only if we are in single mode (for direct output)
    if write_output:
        pruned_root = prune_xml_data(transformed_unpruned_root)
        pruned_count = len(pruned_root)
        
        if pruned_count < transformation_count:
            print(f"  > Pruning: Removed {transformation_count - pruned_count} duplicate formats.")
        
        print(f"  > Success: Kept {pruned_count} unique video entries after pruning.")
        
        # 7. Write pruned output if in Single Mode
        final_output_file = f"{item_id}_pruned.xml"
        try:
            tree = ET.ElementTree(pruned_root) # Use the pruned root
            ET.indent(tree, space="  ", level=0) 
            tree.write(final_output_file, encoding='utf-8', xml_declaration=True)
            print(f"\nSUCCESS: Created single, pruned file: {final_output_file}")
        except IOError as e:
            print(f"Error writing output file {final_output_file}: {e}")
        return None
        
    # Return the *unpruned* but transformed root for batch mode aggregation 
    return transformed_unpruned_root 

def _process_local_folder(folder_path, write_output=False):
    # Existing logic for local folder processing...
    if not os.path.isdir(folder_path):
        print(f"  > Error: Folder path not found: {folder_path}")
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
        print(f"  > Warning: Found no valid video files in {folder_path}.")
        return None
    
    print(f"  > Success: Generated metadata for {file_count} local files.")

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
    
    # Check for flags outside of the standard argparse setup
    is_single_mode = '--single' in sys.argv or '-s' in sys.argv
    keep_original_xml = '--keep-original-xml' in sys.argv

    # Clean up sys.argv for target parsing by removing known flags
    targets = [arg for arg in sys.argv[1:] if arg not in ('--single', '-s', '--keep-original-xml')]

    if not targets or (is_single_mode and len(targets) != 1):
        print("Error: Please provide one or more targets (URL pattern, URL, or local folder).")
        print(f"Usage for Batch: python {sys.argv[0]} <TARGET_1> [TARGET_2] ... [--keep-original-xml]")
        print(f"Usage for Single: python {sys.argv[0]} --single <SINGLE_TARGET> [--keep-original-xml]") 
        sys.exit(1)

    # --- Single File Mode Logic ---
    if is_single_mode:
        target = targets[0]
            
        print("--- Unified Metadata XML Generator (Single File Mode) ---")
        
        # Dispatch to the correct processor, forcing write_output=True
        if target.lower().startswith(('http://', 'https://')):
            # The archive processor handles both saving original/pruned and returns None
            _process_archive_url(target, write_output=True, keep_original=keep_original_xml)
        else:
            _process_local_folder(target, write_output=True)
            
    # --- Batch Mode Logic ---
    else:
        input_targets_patterns = targets
        all_targets = []
        all_results = []
        
        # List to hold the single raw root element before pruning (if requested)
        combined_raw_list = []
        
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
                root_element = _process_archive_url(target, write_output=False, keep_original=keep_original_xml)
            else:
                root_element = _process_local_folder(target, write_output=False)
                
            if root_element is not None:
                all_results.append(root_element)

        if not all_results:
            print("\nFAILURE: No valid metadata could be generated from any of the inputs.")
            sys.exit(1)
            
        # 3. Combine, PRUNE, and write the final unified XML file
        
        # Check if we need to save the combined raw file before pruning
        if keep_original_xml:
            final_root = combine_xml_results(all_results, raw_results_list=combined_raw_list)
        else:
            final_root = combine_xml_results(all_results)
            
        # --- Write the Combined Original File (Before Pruning) ---
        if combined_raw_list:
            final_original_root = combined_raw_list[0]
            final_original_output_file = "combined_metadata_original.xml"
            try:
                tree_orig = ET.ElementTree(final_original_root)
                ET.indent(tree_orig, space="  ", level=0)
                tree_orig.write(final_original_output_file, encoding='utf-8', xml_declaration=True)
                print(f" 💾 Created combined unpruned XML file: {final_original_output_file}")
            except IOError as e:
                print(f"Error writing combined original output file: {e}")

        # --- Write the Combined PRUNED File ---
        final_output_file = "combined_metadata_pruned.xml"
        
        try:
            tree = ET.ElementTree(final_root)
            ET.indent(tree, space="  ", level=0) 
            tree.write(final_output_file, encoding='utf-8', xml_declaration=True)
            print(f"\n=======================================================")
            print(f"SUCCESS: Created unified metadata XML file: {final_output_file}")
            print(f"Total video files aggregated (and pruned): {len(final_root)}")
            print(f"=======================================================")
        except IOError as e:
            print(f"Error writing unified output file {final_output_file}: {e}")