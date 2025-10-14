import xml.etree.ElementTree as ET
import requests
import os
import sys
import glob
import argparse
from typing import List, Dict, Any, Optional, Tuple

# --- Configuration Constants (Must be in a runnable file) ---
SUCCESS_CODES = [200, 301, 302]  # Successful HTTP status codes (OK, Moved, Found)
TIMEOUT_SECONDS = 10
# ---------------------

def discover_and_load_urls(file_paths: List[str]) -> List[Tuple[str, str]]:
    """
    Reads all specified XML files and extracts all video URLs.

    The function correctly uses ElementTree to parse the XML and only extracts
    the 'name' attribute from <file> tags that start with 'http' or 'https'.
    """
    all_urls = []
    
    for xml_path in file_paths:
        # Get just the filename for reporting
        source_file_name = os.path.basename(xml_path) 
        
        try:
            tree = ET.parse(xml_path)
            
            # Look for the <file> tag and extract the 'name' attribute
            for element in tree.findall('.//file'): 
                url = element.get('name')         
                # Only check URLs that start with http/https
                if url and url.lower().startswith(('http://', 'https://')):
                    # Store URL and the file it came from
                    all_urls.append((url, source_file_name))
            
        except ET.ParseError as e:
            print(f"❌ Error parsing XML in {source_file_name}: {e}")
        except Exception as e:
            print(f"❌ Unexpected error reading {source_file_name}: {e}")

    print(f"Successfully loaded {len(all_urls)} URLs from {len(file_paths)} XML files.\n")
    return all_urls

def validate_remote_urls(urls_with_sources: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """
    Performs a non-intrusive HEAD request on each URL to check reachability.
    
    Returns:
        list: A list of dictionaries containing the validation report.
    """
    report = []
    total_urls = len(urls_with_sources)
    
    # Use requests.Session for efficient connection reuse
    with requests.Session() as session:
        for i, (url, source_file) in enumerate(urls_with_sources):
            # Print a progress indicator using \r (carriage return) to overwrite the line
            print(f"Checking URL {i+1}/{total_urls} (Source: {source_file}): {url[:50]}...", end='\r', flush=True)

            try:
                # Use HEAD request for speed and minimal data transfer
                response = session.head(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
                
                status_code = response.status_code
                
                if status_code in SUCCESS_CODES:
                    status = "PASS"
                    message = f"Reachable. Status: {status_code}"
                elif status_code == 404:
                    status = "FAIL"
                    message = f"Not Found. Status: 404"
                else:
                    status = "WARNING"
                    message = f"Unexpected Status: {status_code}. Requires manual review."

            except requests.exceptions.Timeout:
                status = "FAIL"
                message = f"Timeout after {TIMEOUT_SECONDS}s. Network issue or server too slow."
            except requests.exceptions.ConnectionError:
                status = "FAIL"
                message = "Connection Error (DNS or Server unreachable/blocked)."
            except Exception as e:
                status = "FAIL"
                message = f"An unexpected error occurred: {type(e).__name__}"

            report.append({
                'url': url,
                'source_file': source_file,
                'status': status,
                'message': message
            })
            sys.stdout.flush() 

    # Clear the progress indicator line after completion
    print("                                                                                    ", end='\r')
    return report

def generate_report(report_data: List[Dict[str, Any]]):
    """
    Prints the final validation results and overall summary.
    Prints detailed results only if failures or warnings are present.
    """
    
    print("\n" + "="*80)
    print("                 AGGREGATE REMOTE URL VALIDATION REPORT")
    print("="*80)
    
    passed_count = sum(1 for item in report_data if item['status'] == 'PASS')
    failed_count = sum(1 for item in report_data if item['status'] == 'FAIL')
    warn_count = sum(1 for item in report_data if item['status'] == 'WARNING')
    total_count = len(report_data)

    print(f"OVERALL SUMMARY: {total_count} URLs checked across all files.")
    print(f"  {passed_count} PASS, {failed_count} FAIL, {warn_count} WARNING.")
    print("-" * 80)
    
    # --- LOGIC FOR CONCISE REPORTING ---
    if failed_count > 0 or warn_count > 0:
        # Print detailed report grouped by file only if there are issues
        current_file: Optional[str] = None
        
        for item in report_data:
            if item['source_file'] != current_file:
                current_file = item['source_file']
                print(f"\n--- Results for {current_file} ---")

            print(f"[{item['status']: <7}] {item['url']}")
            print(f"{' ': <10}-> {item['message']}")
        
        print("="*80)
        
        if failed_count > 0:
            print(f"\n*** ACTION REQUIRED: {failed_count} URL(s) marked FAIL. Please check these files. ***")
        elif warn_count > 0:
            print(f"\nReview Recommended: {warn_count} URL(s) marked WARNING (Non-success status code).")
            
    else:
        # Concise success message if everything passed
        print("✅ ALL OK: No failures or warnings found. Detailed report skipped.")
    # --- END LOGIC ---


if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(
        description="Scans XML files in a specified directory for dead video links."
    )
    
    parser.add_argument(
        'directory',
        type=str,
        help="The directory path containing the XML files (e.g., /home/user/channel_configs)."
    )
    
    parser.add_argument(
        '-p', '--pattern',
        type=str,
        default='*.xml',
        help="The glob pattern to match within the directory (e.g., *.xml or bbc_*.xml). Defaults to '*.xml'."
    )

    args = parser.parse_args()

    # 1. Discover all XML files in the specified directory
    search_pattern = os.path.join(args.directory, args.pattern)
    all_xml_files = glob.glob(search_pattern)

    if not all_xml_files:
        print(f"❌ ERROR: No files matching '{args.pattern}' found in '{args.directory}'")
        sys.exit(0)
    
    print(f"Found {len(all_xml_files)} file(s) for processing using pattern '{args.pattern}' in '{args.directory}'")
    
    # 2. Load the list of URLs from the XML files
    remote_urls_with_sources = discover_and_load_urls(all_xml_files)
    
    if remote_urls_with_sources:
        # 3. Run the validation checks
        validation_report = validate_remote_urls(remote_urls_with_sources)
        
        # 4. Print the results
        generate_report(validation_report)
    else:
        print("No valid remote HTTP/HTTPS URLs found in the discovered XML files.")
