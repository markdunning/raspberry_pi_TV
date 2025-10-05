import datetime
import os
import glob
import argparse
import json
import xml.etree.ElementTree as ET
import random
import csv

# --- Configuration Constants (ADJUST THESE PATHS TO YOUR SYSTEM) ---
# Directory where your XML files (e.g., bbc_channel.xml) and channel_list.json live
SCHEDULE_CONFIG_DIR = '/home/markd/raspberry_pi_TV/channel_configs/' 
# Base content directory (used to determine content_root for channels)
CONTENT_BASE_DIR = '/home/markd/rasppery_pi_TV/'
# Directory where the output schedules will be saved
OUTPUT_SCHEDULE_DIR = '/home/markd/raspberry_pi_TV/schedule_data/' 

# Time constants
SLOT_DURATION_SECONDS = 1800 # 30 minutes (Adjust for finer/coarser granularity)
SLOT_DURATION = datetime.timedelta(seconds=SLOT_DURATION_SECONDS)

# File naming templates
DATE_FORMAT = "%Y-%m-%d"
JSON_FILENAME_TEMPLATE = "{channel_name}_{date}_schedule.json"
CSV_FILENAME_TEMPLATE = "{channel_name}_{date}_schedule.csv"


# --- Utility Functions ---

def parse_date(date_str):
    """Parses a string into a datetime.date object."""
    try:
        return datetime.datetime.strptime(date_str, DATE_FORMAT).date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Date must be in {DATE_FORMAT} format (e.g., 2025-10-05)")


def get_content_from_file(xml_path):
    """
    Reads a single content XML file and extracts one video entry 
    based on the <file name="..."> <length>...</length> </file> structure.

    Args:
        xml_path (str): The full path to the single show XML file.

    Returns:
        dict or None: A single dictionary {'path': '...', 'duration': ...} or None.
    """
    if not os.path.exists(xml_path):
        return None

    try:
        tree = ET.parse(xml_path)
        
        # Look for the first 'file' tag, assuming one video per XML file
        file_tag = tree.find('.//file') 
        
        if file_tag is not None:
            path = file_tag.get('name')
            length_tag = file_tag.find('length')
            duration_str = length_tag.text if length_tag is not None else None
            
            if path and duration_str:
                try:
                    duration = float(duration_str)
                    return {'path': path, 'duration': duration}
                except ValueError:
                    print(f"❌ Error: Invalid length value '{duration_str}' in {xml_path}")
        
    except ET.ParseError as e:
        print(f"❌ Error parsing Content XML {xml_path}: {e}")
        
    return None


def get_videos_from_xml_file(xml_path):
    """
    Reads a single show/playlist XML file and extracts ALL video entries 
    based on the <file name="..."> <length>...</length> </file> structure.

    Args:
        xml_path (str): The full path to the single show XML file.

    Returns:
        list: A list of dictionaries [{'path': '...', 'duration': ...}, ...]
    """
    video_list = []
    
    if not os.path.exists(xml_path):
        return video_list

    try:
        tree = ET.parse(xml_path)
        
        # Look for all 'file' tags within the document
        for file_tag in tree.findall('.//file'): 
            path = file_tag.get('name')
            length_tag = file_tag.find('length')
            duration_str = length_tag.text if length_tag is not None else None
            
            if path and duration_str:
                try:
                    duration = float(duration_str)
                    video_list.append({
                        'path': path, 
                        'duration': duration
                    })
                except ValueError:
                    print(f"❌ Error: Invalid length value '{duration_str}' in {xml_path}")
        
    except ET.ParseError as e:
        print(f"❌ Error parsing Content XML {xml_path}: {e}")
        
    return video_list

# schedule_generator.py (Revised assign_random_video)

def assign_random_video(slot_name, content_manifest, channel_content_root, slot_folder):
    """
    Implements Two-Stage Randomization: 
    1. Randomly selects a show/playlist XML file from the folder.
    2. Randomly selects one video from within that XML file.
    """
    
    # 1. Define the content folder path
    content_folder_path = os.path.join(channel_content_root, slot_folder)
    
    # --- Manifest Caching Logic ---
    # We now cache a list of ALL content XML file paths for the slot folder.
    if slot_folder not in content_manifest:
        xml_search_pattern = os.path.join(content_folder_path, '*.xml')
        content_manifest[slot_folder] = glob.glob(xml_search_pattern)
            
    available_xml_files = content_manifest.get(slot_folder, [])
    
    if not available_xml_files:
        return None, "NO CONTENT"

    # --- STAGE 1: Randomly select a Show XML file ---
    chosen_xml_path = random.choice(available_xml_files)
    
    # --- STAGE 2: Get all videos from the chosen XML and select one ---
    show_videos = get_videos_from_xml_file(chosen_xml_path)
    
    if not show_videos:
        # If the XML file was chosen but contained no videos
        print(f"⚠️ Warning: Chosen XML file {os.path.basename(chosen_xml_path)} contains no valid video entries.")
        return None, "NO CONTENT"

    # Select one video (e.g., one episode) from the list in that XML
    main_video_data = random.choice(show_videos)
    
    # Determine Show Name from the XML filename (e.g., "Pingu.xml" -> "Pingu")
    show_name = os.path.basename(chosen_xml_path).split('.')[0]
    
    # The video data needs the show name attached for tracking/logging
    main_video_data['show_name'] = show_name
    
    return main_video_data, show_name

# --- Worker Function (Generates Schedule for One Channel/One Day) ---

# --- Helper Function (Re-included for context and clarity) ---
def calculate_buffer(actual_duration_seconds, round_to_minutes=5):
    """Calculates the buffer needed to round the duration up to the nearest multiple of the round_to_minutes."""
    
    ROUND_TO_SECONDS = round_to_minutes * 60
    
    if actual_duration_seconds <= 0:
        return ROUND_TO_SECONDS, 0.0 # Default to 5 minutes total slot if duration is zero

    # Calculate the next multiple of ROUND_TO_SECONDS
    num_slots = int((actual_duration_seconds + ROUND_TO_SECONDS - 1) / ROUND_TO_SECONDS)
    total_slot_seconds = num_slots * ROUND_TO_SECONDS

    # Calculate the buffer
    buffer_seconds = total_slot_seconds - actual_duration_seconds
    
    return total_slot_seconds, buffer_seconds

# ------------------------------------------------------------------------------------------------

def generate_schedule_for_channel(xml_path, inferred_channel_name, schedule_date_str, overwrite_mode):
    
    # 0. Setup
    daily_schedule = []
    
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    # Read channel attributes
    CHANNEL_NAME = root.get('name', inferred_channel_name)
    start_time_str = root.get('start_time')
    end_time_str = root.get('end_time')
    channel_content_root = root.get('content_root')
    
    # Setup datetime objects
    schedule_date = datetime.datetime.strptime(schedule_date_str, "%Y-%m-%d").date()
    start_time_dt = datetime.datetime.strptime(start_time_str, '%H:%M').replace(year=schedule_date.year, month=schedule_date.month, day=schedule_date.day)
    end_time_dt = datetime.datetime.strptime(end_time_str, '%H:%M').replace(year=schedule_date.year, month=schedule_date.month, day=schedule_date.day)

    # Handle schedules that run past midnight
    if end_time_dt <= start_time_dt:
        end_time_dt += datetime.timedelta(days=1)
    
    current_datetime = start_time_dt
    
    # Load slot definitions from XML
    slot_definitions = []
    for slot_tag in root.findall('slot'):
        slot_start_time = datetime.datetime.strptime(slot_tag.get('start'), '%H:%M').time()
        slot_end_time = datetime.datetime.strptime(slot_tag.get('end'), '%H:%M').time()

        slot_definitions.append({
            'name': slot_tag.get('name'),
            'start': slot_start_time,
            'end': slot_end_time,
            'folder': slot_tag.get('folder'),
            'filler_xml': slot_tag.get('filler_xml'),
        })
        
    # Global Content Manifest (caches content XML file paths)
    content_manifest = {} 
    
    # --- 1. Iterate and schedule content block-by-block (Dynamic Duration) ---
    while current_datetime < end_time_dt:
        
        # 1a. Find the correct slot definition for the current time
        current_slot_name_def = None
        current_time_only = current_datetime.time()
        
        for slot_def in slot_definitions:
            slot_start = slot_def['start']
            slot_end = slot_def['end']
            
            # --- Slot Check Logic (Handles Midnight Crossover) ---
            if slot_start < slot_end:
                # Slot does NOT cross midnight (e.g., 07:00 to 12:00)
                if slot_start <= current_time_only < slot_end:
                    current_slot_name_def = slot_def
                    break
            else:
                # Slot DOES cross midnight (e.g., 21:00 to 01:00)
                # Active if current_time >= start OR current_time < end
                if current_time_only >= slot_start or current_time_only < slot_end:
                    current_slot_name_def = slot_def
                    break
        
        # 1b. Determine the assignment based on whether a slot was found
        if current_slot_name_def:
            # --- Assignment for Active Slot ---
            current_slot_name = current_slot_name_def['name']
            current_slot_folder = current_slot_name_def['folder']
            filler_xml_key = current_slot_name_def['filler_xml']
            
            # Call the Two-Stage Random Assignment function
            main_video_data, show_name = assign_random_video(
                current_slot_name, 
                content_manifest, 
                channel_content_root, 
                current_slot_folder
            )
            
            if main_video_data and main_video_data.get('duration', 0.0) > 0:
                actual_video_duration = main_video_data['duration']
                
                # Calculate the total slot duration (video + buffer)
                total_slot_duration, buffer_seconds = calculate_buffer(actual_video_duration, round_to_minutes=5)
                
                # Update main_video_data for the player script
                main_video_data['buffer_seconds'] = buffer_seconds
                main_video_data['actual_duration'] = actual_video_duration
                
                slot_duration_seconds = total_slot_duration
                
            else:
                # Fallback for "NO CONTENT" (5 min total slot)
                slot_duration_seconds = 300.0 
                main_video_data = {'path': 'NO CONTENT', 'duration': 0.0, 'buffer_seconds': 0.0}
                show_name = "NO CONTENT"
                filler_xml_key = None # No filler for NO CONTENT slot

        else:
            # --- Assignment for Off-Air Time ---
            current_slot_name = "OFF_AIR_TIME"
            slot_duration_seconds = 3600.0 
            main_video_data = {'path': 'N/A', 'duration': slot_duration_seconds, 'buffer_seconds': 0.0}
            show_name = "Off Air"
            filler_xml_key = None
            
        # 1c. Save the scheduled item
        schedule_item = {
            'start_time': current_datetime,
            'channel_name': CHANNEL_NAME,
            'slot_name': current_slot_name,
            'video_data': main_video_data, 
            'show_name': show_name,
            'slot_duration': slot_duration_seconds,
            'filler_xml_path': filler_xml_key
        }
        daily_schedule.append(schedule_item)
        
        # Advance the time using the TOTAL SLOT duration
        current_datetime += datetime.timedelta(seconds=slot_duration_seconds) 
    
    
    # --- 2. Serialization and Output ---
    
    serializable_schedule_json = []
    serializable_schedule_csv = []
    
    csv_fieldnames = ['start_time', 'channel_name', 'slot_name', 'show_name', 'slot_duration_total', 
                      'video_duration_actual', 'buffer_duration', 'main_video_path', 'filler_xml_path']

    for item in daily_schedule:
        
        # CRASH FIX: Ensure video_data is valid
        main_video_data = item['video_data']
        is_valid_video = main_video_data and 'path' in main_video_data
        
        if is_valid_video:
            main_video_path = main_video_data['path']
            # Use 'actual_duration' if set, otherwise fallback to 'duration' or 0.0
            main_video_duration = main_video_data.get('actual_duration', main_video_data.get('duration', 0.0))
            buffer_seconds = main_video_data.get('buffer_seconds', 0.0)
        else:
            main_video_path = 'N/A'
            main_video_duration = 0.0
            buffer_seconds = 0.0

        # JSON Output
        item_json = {
            'start_time': item['start_time'].isoformat(),
            'channel_name': item['channel_name'],
            'slot_name': item['slot_name'],
            'show_name': item['show_name'],
            'slot_duration_total': item['slot_duration'],
            'video_data': {
                'path': main_video_path, 
                'duration': main_video_duration,
                'buffer_seconds': buffer_seconds
            },
            'filler_xml_path': item['filler_xml_path']
        }
        serializable_schedule_json.append(item_json)

        # CSV Output
        serializable_schedule_csv.append({
            'start_time': item_json['start_time'],
            'channel_name': item['channel_name'],
            'slot_name': item['slot_name'],
            'show_name': item['show_name'],
            'slot_duration_total': f"{item['slot_duration'] / 60.0:.2f} min",
            'video_duration_actual': f"{main_video_duration:.2f} sec",
            'buffer_duration': f"{buffer_seconds:.2f} sec",
            'main_video_path': main_video_path,
            'filler_xml_path': item['filler_xml_path']
        })

    # --- 3. Write Files with Overwrite Control ---
    
    # (Implementation of file writing using OUTPUT_SCHEDULE_DIR and the serializable lists)
    
    # Placeholder for file writing (Assuming global variables are used here)
    output_json_path = os.path.join(
        OUTPUT_SCHEDULE_DIR, 
        JSON_FILENAME_TEMPLATE.format(channel_name=CHANNEL_NAME, date=schedule_date_str)
    )
    output_csv_path = os.path.join(
        OUTPUT_SCHEDULE_DIR, 
        CSV_FILENAME_TEMPLATE.format(channel_name=CHANNEL_NAME, date=schedule_date_str)
    )

    # Check Overwrite Status
    if not overwrite_mode and os.path.exists(output_json_path):
        print(f"  ℹ️ Skipping {CHANNEL_NAME} {schedule_date_str}: File exists and overwrite is off.")
        return 
        
    # Write JSON
    try:
        with open(output_json_path, 'w') as f:
            json.dump(serializable_schedule_json, f, indent=4)
        print(f"  ✅ JSON: {CHANNEL_NAME} schedule saved to {os.path.basename(output_json_path)}")
    except Exception as e:
        print(f"  ❌ Error saving JSON schedule for {CHANNEL_NAME}: {e}")

    # Write CSV
    # (The actual CSV writing block should be here, similar to the JSON block)
    try:
        with open(output_csv_path, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=csv_fieldnames)
            writer.writeheader()
            # Must ensure row dictionaries contain all keys from csv_fieldnames
            writer.writerows(serializable_schedule_csv)
        print(f"  ✅ CSV: {CHANNEL_NAME} schedule saved to {os.path.basename(output_csv_path)}")
    except Exception as e:
        print(f"  ❌ Error saving CSV schedule for {CHANNEL_NAME}: {e}")

# --- Main Execution Loop (Handles Date Range and Channel Discovery) ---

def generate_all_schedules(start_date, offset, overwrite_mode):
    """
    Scans the configuration directory and generates schedules across a date range.
    """
    
    # 1. Discover all channel configuration files
    search_pattern = os.path.join(SCHEDULE_CONFIG_DIR, '*_channel.xml')
    channel_xml_files = glob.glob(search_pattern)
    
    if not channel_xml_files:
        print(f"❌ ERROR: No channel schedule XML files found in {SCHEDULE_CONFIG_DIR}")
        return

    # 2. Iterate through the date range
    for day_offset in range(offset + 1):
        target_date = start_date + datetime.timedelta(days=day_offset)
        target_date_str = target_date.strftime(DATE_FORMAT)
        
        print(f"\n--- Generating Schedules for Date: {target_date_str} ---")
        
        # 3. Iterate through each channel XML file
        for xml_path in channel_xml_files:
            try:
                filename = os.path.basename(xml_path)
                inferred_channel_name = filename.split('_channel.xml')[0]
                
                # Call the worker function with the date and overwrite flag
                generate_schedule_for_channel(
                    xml_path, 
                    inferred_channel_name, 
                    target_date_str, 
                    overwrite_mode
                )
                
            except Exception as e:
                print(f"❌ CRITICAL ERROR processing {xml_path} for {target_date_str}: {e}")

    print("\n--- All scheduled generations complete. ---")


if __name__ == '__main__':
    
    parser = argparse.ArgumentParser(
        description="Generate daily schedules for all configured channels."
    )
    
    parser.add_argument(
        '-s', '--start-date', 
        type=parse_date,
        default=datetime.date.today(),
        help=f"The start date for schedule generation (format: {DATE_FORMAT}). Defaults to today."
    )
    
    parser.add_argument(
        '-o', '--offset', 
        type=int,
        default=0,
        help="Number of additional days to generate schedules for (e.g., 6 for one week total)."
    )
    
    parser.add_argument(
        '-w', '--overwrite', 
        action='store_true',
        help="If set, existing schedule files for the target dates will be overwritten."
    )
    
    args = parser.parse_args()
    
    # Ensure the output directory exists
    os.makedirs(OUTPUT_SCHEDULE_DIR, exist_ok=True) 
    
    generate_all_schedules(args.start_date, args.offset, args.overwrite)