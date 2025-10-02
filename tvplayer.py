import xml.etree.ElementTree as ET
import random
import subprocess
import time
import datetime
import logging
import sys
import os
import glob
import urllib.parse  # ⬅️ NEW IMPORT for URL encoding

# --- 1. CONFIGURATION ---

# Base path where your content folders are located
BASE_CONTENT_PATH = os.path.dirname(os.path.abspath(__file__))

# Setup logging
LOG_DIR = os.path.join(BASE_CONTENT_PATH, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILENAME = datetime.datetime.now().strftime("tvplayer_%Y%m%d_%H%M%S.log")
LOG_PATH = os.path.join(LOG_DIR, LOG_FILENAME)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout)
    ]
)
logging.info(f"Log file created: {LOG_PATH}")

# --- 2. VLC PLAYBACK COMMAND CONFIGURATIONS ---

# ⚠️ Use the flags confirmed to be working: 
WORKING_VOUT_FLAG = 'x11' 
WORKING_AOUT_DEVICE = 'sysdefault:CARD=Headphones' 

# Base flags common to all playback
BASE_FLAGS = [
    '--vout', WORKING_VOUT_FLAG, 
    '--aout', 'alsa', 
    '--alsa-audio-device', WORKING_AOUT_DEVICE, 
    '--fullscreen',
]

# Flags specific to remote streaming (network caching and resilience)
REMOTE_STREAMING_FLAGS = [
    '--network-caching', '5000', # Increased cache for resilience
    '--http-reconnect',           # Force re-connect on stream interruption
    '--demux-filter', 'demux_ts',
]

# --- 3. HELPER FUNCTIONS ---

def get_video_duration(file_path):
    """
    Placeholder for video duration. Needs an accurate method (like ffprobe) 
    for real-world use if durations are not in XML.
    """
    if file_path.lower().startswith(('http://', 'https://')):
        return 120.0 # Default 2 minutes for remote
    
    return random.randint(60, 300) # Default 1-5 minutes for local

def load_content():
    """Scans all time-slot folders for XML show manifest files."""
    content = {
        'ads': [],
        'morning': [],
        'afternoon': [],
        'evening': []
    }
    xml_extension = '*.xml'
    total_xml_files = 0

    # Scan for XML show manifests in ALL folders
    for slot_name in content.keys():
        folder_path = os.path.join(BASE_CONTENT_PATH, slot_name)
        
        if os.path.isdir(folder_path):
            xml_files = glob.glob(os.path.join(folder_path, xml_extension))
            content[slot_name].extend(xml_files)
            total_xml_files += len(xml_files)

    logging.info(f"Successfully aggregated {total_xml_files} show manifest XML files.")
    return content

def get_random_entry_from_xml(xml_file_path):
    """
    Picks one random video entry from the specified XML file, using the 
    <file name='...'> structure.
    """
    try:
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
        all_entries = root.findall('.//file')
        
        if not all_entries:
            logging.warning(f"No <file> entries found in {xml_file_path}. Skipping.")
            return None
            
        file_element = random.choice(all_entries)
        
        file_path = file_element.get('name')
        length_element = file_element.find('length')
        
        if file_path and length_element is not None:
            try:
                duration = float(length_element.text)
                return {'path': file_path, 'duration': duration}
            except (TypeError, ValueError):
                logging.error(f"Invalid duration found in {xml_file_path}.")
                return None
        else:
            logging.error(f"Incomplete entry (missing 'name' attribute or <length> tag) in {xml_file_path}.")
            return None
            
    except ET.ParseError:
        logging.error(f"Failed to parse XML file at {xml_file_path}.")
        return None
    except Exception as e:
        logging.error(f"Error processing {xml_file_path}: {e}")
        return None


def get_current_slot():
    """Determines the current time slot based on the Pi's system time."""
    current_hour = datetime.datetime.now().hour
    
    if 6 <= current_hour < 12:
        return 'morning'
    elif 12 <= current_hour < 18:
        return 'afternoon'
    else:
        return 'evening'

def play_video(video_data):
    """
    Plays a video file (local or remote) using cvlc.
    """
    path = video_data['path']
    duration = video_data['duration']
    
    is_remote = path.lower().startswith(('http://', 'https://'))
    
    command = [
        'cvlc', 
        '--no-video-title',
        '--play-and-exit',
        '--quiet',              
        '--one-instance',
    ]
    
    if is_remote:
        # ⬅️ FIX B IMPLEMENTATION: Encode spaces and special characters in the URL path
        try:
            parsed_url = urllib.parse.urlparse(path)
            # Encode the path part, leaving the scheme/netloc intact
            encoded_path = parsed_url._replace(path=urllib.parse.quote(parsed_url.path)).geturl()
        except Exception:
            # Fallback if parsing fails
            encoded_path = path

        logging.info(f"START Streaming: {encoded_path} (Duration: {duration:.2f}s)")
        command.extend(REMOTE_STREAMING_FLAGS)
        command.extend(BASE_FLAGS)
        command.append(encoded_path) # Use the encoded path
    else:
        local_path = os.path.join(BASE_CONTENT_PATH, path)
        logging.info(f"START Local Playback: {local_path} (Duration: {duration:.2f}s)")
        command.extend(BASE_FLAGS)
        command.append(local_path)

    try:
        timeout_buffer = duration + 10 
        logging.info(f"Executing command: {' '.join(command)}")
        subprocess.run(command, timeout=timeout_buffer, check=True)
        
    except subprocess.TimeoutExpired:
        logging.warning(f"Playback timed out for {path}. VLC hung or took too long.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Playback FAILED for {path}. Error code: {e.returncode}. Check VLC logs for details.")
    except Exception as e:
        logging.error(f"An unexpected error occurred during playback of {path}: {e}")
        
    logging.info(f"END Playback: {path}")

def run_ad_break(content):
    """Picks a random ad XML file and plays a random ad from it."""
    ads_xml_list = content.get('ads', [])
    if not ads_xml_list:
        logging.warning("AD BREAK SKIPPED: No ad XML files available.")
        return

    num_ads = min(3, len(ads_xml_list))
    selected_ads_xmls = random.sample(ads_xml_list, num_ads)

    logging.info(f"AD BREAK: Starting commercial break with {num_ads} random ad manifest(s).")
    
    for i, ad_xml_path in enumerate(selected_ads_xmls, 1):
        ad_data = get_random_entry_from_xml(ad_xml_path)
        
        if ad_data:
            logging.info(f"AD PLAY: Ad #{i} selected from manifest: {os.path.basename(ad_xml_path)}")
            play_video(ad_data)
        else:
            logging.warning(f"AD PLAY: Skipping ad from {os.path.basename(ad_xml_path)} due to entry error.")
        
        time.sleep(1)

# --- 4. MAIN LOOP ---

def main():
    content_manifest = load_content()
    
    if not any(content_manifest.values()):
        logging.error("FATAL: No XML content manifests found in any folders. Check directory structure.")
        return

    logging.info("TV Channel Simulator initialization complete. Starting loop...")

    while True:
        current_slot = get_current_slot()
        main_show_xml_list = content_manifest.get(current_slot, [])

        logging.info(f"--- SCHEDULER: Slot is {current_slot.upper()} (Hour {datetime.datetime.now().strftime('%H:%M')}) ---")

        if main_show_xml_list:
            random_show_xml = random.choice(main_show_xml_list)
            main_video_data = get_random_entry_from_xml(random_show_xml)

            if main_video_data:
                logging.info(f"MAIN CONTENT: Selected show {os.path.basename(random_show_xml)} and video {main_video_data['path']}")
                play_video(main_video_data)
            else:
                logging.warning(f"WARNING - Skipped show {os.path.basename(random_show_xml)} due to error or no playable entries.")

        else:
            logging.warning(f"WARNING - No show XMLs available for the {current_slot} slot. Falling back to filler (ads).")
            time.sleep(5) 

        run_ad_break(content_manifest)

        time.sleep(5) 


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Script manually stopped by user (Ctrl+C). Exiting.")
        print("\nExiting script.")