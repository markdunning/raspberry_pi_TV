import xml.etree.ElementTree as ET
import os
import glob
import argparse
import random

# --- Configuration: Preferred format order (Highest to Lowest Priority) ---
FORMAT_PRIORITY = [
    '.mp4',    # Score 0: Highest priority
    '.m4v',    # Score 1
    '.mov',    # Score 2
    '.webm',   # Score 3
    '.ogv',    # Score 4
    '.mkv',    # Score 5
    '.ia.mp4', # Score 6
    '.avi',
]

PRIORITY_MAP = {ext: i for i, ext in enumerate(FORMAT_PRIORITY)}

def get_file_extension(filename):
    """Safely extracts the file extension from a path, prioritizing multi-part extensions."""
    for ext in FORMAT_PRIORITY:
        if filename.lower().endswith(ext):
            return ext
            
    return os.path.splitext(filename.split('/')[-1])[1].lower()

def extract_base_filename(url_or_path):
    """Strips the path and the file extension(s) to get the base episode identifier for grouping."""
    base_name_with_ext = os.path.basename(url_or_path)
    
    # Sort extensions by length descending to strip multi-part extensions like '.ia.mp4' first
    stripped_name = base_name_with_ext
    for ext in sorted(FORMAT_PRIORITY, key=len, reverse=True):
        if stripped_name.lower().endswith(ext):
            # Only strip the longest matching extension
            stripped_name = stripped_name[:-len(ext)]
            break # Stop after finding and stripping the most specific extension
    
    return stripped_name.strip()


def prune_duplicates_in_xml(input_path, output_path, unique_tag_name='original'):
    """
    Reads an XML file, prunes duplicates based on format priority, and reports 
    when duplicates are found and which one is kept. The unique key is now
    always derived from the file name without extension.
    """
    
    unique_episodes = {}
    
    if not os.path.exists(input_path):
        print(f"❌ Error: Input file not found at {input_path}")
        return False
        
    try:
        tree = ET.parse(input_path)
        root = tree.getroot()
        
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
            
            # Check if we have seen this unique episode before
            if unique_key not in unique_episodes:
                # First time seeing this episode
                unique_episodes[unique_key] = (priority_score, file_tag)
                
            else:
                # Duplicate found!
                current_best_score, current_best_tag = unique_episodes[unique_key]
                current_best_name = current_best_tag.get('name')
                
                print(f"    🔎 Duplicate found for: {unique_key}")
                print(f"       - Existing Best: {os.path.basename(current_best_name)} (Score: {current_best_score})")
                print(f"       - New Candidate: {os.path.basename(file_name)} (Score: {priority_score})")
                
                # Compare scores (lower score is better/higher priority)
                if priority_score < current_best_score:
                    # New candidate is better! Replace the stored element.
                    unique_episodes[unique_key] = (priority_score, file_tag)
                    print(f"       -> DECISION: Keeping the new candidate ({file_ext}).")
                else:
                    # Existing is better or equal priority. Keep the existing one.
                    print(f"       -> DECISION: Keeping the existing best.")

        # 2. Build the New XML Tree
        new_root = ET.Element(root.tag, root.attrib)
        
        for key, value in root.attrib.items():
            new_root.set(key, value)

        # Sort by key for deterministic output order
        sorted_episodes = sorted(unique_episodes.items())
        
        for _, (_, element) in sorted_episodes:
            new_root.append(element)
            
        new_tree = ET.ElementTree(new_root)
        
        # 3. Write the New XML File
        try:
            ET.indent(new_tree, space="  ", level=0)
        except AttributeError:
            pass 
            
        new_tree.write(output_path, encoding='utf-8', xml_declaration=True)
        return True

    except ET.ParseError as e:
        print(f"❌ Error parsing XML {input_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error processing {input_path}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Prune duplicate video format entries from content XML files, prioritizing formats.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        'input_dir', 
        type=str,
        help="The directory containing the XML files to be pruned (e.g., bbc/morning)."
    )
    parser.add_argument(
        '-o', '--output-suffix',
        type=str,
        default='_pruned',
        help="Suffix to add to the new pruned filename. (e.g., 'show.xml' becomes 'show_pruned.xml')."
    )
    parser.add_argument(
        '-t', '--unique-tag',
        type=str,
        default='original',
        help="The sub-element tag that identifies a unique episode (This argument is now ignored as the key is derived from the filename)."
    )
    
    args = parser.parse_args()
    
    search_pattern = os.path.join(args.input_dir, '*.xml')
    xml_files = glob.glob(search_pattern)
    
    if not xml_files:
        print(f"⚠️ Warning: No XML files found in {args.input_dir}")
        return

    print(f"Found {len(xml_files)} XML files. Starting format-prioritized pruning...")

    for input_file in xml_files:
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}{args.output_suffix}{ext}"
        
        print(f"\n--- Processing {os.path.basename(input_file)} ---")
        
        # Note: The unique_tag argument is passed but functionally ignored inside the pruning function.
        if prune_duplicates_in_xml(input_file, output_file, args.unique_tag):
            print(f"--- ✅ Pruned file saved as {os.path.basename(output_file)} ---")
        else:
            print(f"--- ❌ Failed to prune {os.path.basename(input_file)} ---")

if __name__ == '__main__':
    main()