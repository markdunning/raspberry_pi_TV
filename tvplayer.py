import subprocess
import time
import logging
import os
import json 
from datetime import datetime, timedelta, date, time as dt_time
from urllib.parse import quote 
import xml.etree.ElementTree as ET
import random
from typing import Dict, Any, Optional, List
import sys 

# --- Global Configuration ---
LOG_DIR = "logs"
SCHEDULE_DIR = "schedule_data" # User specified directory
CHANNEL_ROOT = os.path.join(os.path.dirname(__file__), 'channel_configs') # User specified directory
DEFAULT_CHANNEL = 'bbc' # The fallback if no channel is provided on the command line
# --- Ident Logic Configuration ---
IDENT_MAX_DURATION_SECONDS = 90 # Use idents for gaps 90 seconds or less
MANDATORY_IDENT_MAX_SECONDS = 15 # Max time for the forced ident between shows
# ---------------------------------

# --- Logging Setup ---
def setup_logging(channel_name):
    """Initializes logging and returns the log file path."""
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = os.path.join(LOG_DIR, f"{timestamp}_{channel_name}player.log")
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(funcName)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    logging.info(f"Logging initialized. Output saved to: {log_file}")
    return log_file

logger = logging.getLogger(__name__)

# --- Helper Functions ---

def load_video_paths_from_xml(xml_path: str) -> List[str]:
    """
    Reads the list of video paths from a simple XML file.
    It expects video paths listed under <file> tags with a 'name' attribute,
    consistent across idents and filler lists.
    """
    paths = []
    logger.debug(f"Attempting to load XML from: {xml_path}") 
    
    if not os.path.exists(xml_path):
        logger.warning(f"Video list XML file not found at: {xml_path}")
        return paths
        
    try:
        tree = ET.parse(xml_path)
        # Look for the <file> tag and extract the 'name' attribute
        for element in tree.findall('.//file'): 
            path = element.get('name')         
            if path:
                paths.append(path)
        
        if paths:
            logger.info(f"Successfully loaded {len(paths)} video paths from {xml_path}.")
        else:
            logger.warning(f"XML file found at {xml_path} but contained NO valid <file name='...'> elements.")
            
    except Exception as e:
        logger.error(f"Error loading videos from {xml_path}: {e}")
    return paths

def load_channel_config(channel_name: str) -> Optional[Dict[str, Any]]:
    """
    Loads channel configuration, including global ident path and all slot configurations.
    """
    xml_path = os.path.join(CHANNEL_ROOT, f"{channel_name}_channel.xml")
    
    if not os.path.exists(xml_path):
        logger.error(f"Channel configuration XML not found at: {xml_path}")
        return None

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        config = {
            'name': root.get('name', channel_name),
            'ident_xml_name': root.get('ident_xml'), 
            'content_root': root.get('content_root', ''),
            'slots': [] # List to hold slot configurations
        }
        
        # Load slot configurations, including the slot-specific filler_xml
        for slot_element in root.findall('slot'):
            slot_start_str = slot_element.get('start')
            slot_end_str = slot_element.get('end')
            
            # Convert time strings to datetime.time objects for easy comparison later
            slot_start_time = datetime.strptime(slot_start_str, '%H:%M').time()
            slot_end_time = datetime.strptime(slot_end_str, '%H:%M').time()
            
            config['slots'].append({
                'name': slot_element.get('name'),
                'start_time': slot_start_time,
                'end_time': slot_end_time,
                # Slot-specific filler path for fallback content (now includes adverts)
                'filler_xml_name': slot_element.get('filler_xml'), 
                'folder': slot_element.get('folder') 
            })
            
        logger.info(f"Loaded channel config for '{channel_name}'. Global Ident: {config['ident_xml_name']} and {len(config['slots'])} slots.")
        return config

    except Exception as e:
        logger.error(f"Error loading channel config {xml_path}: {e}")
        return None

def get_active_slot_config(target_time: dt_time, channel_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Finds the active slot configuration based on the current time.
    Handles wrap-around slots (e.g., 21:00 to 01:00).
    """
    for slot in channel_config['slots']:
        start_time = slot['start_time']
        end_time = slot['end_time']
        
        # Simple case (start before end, e.g., 07:00-12:00)
        if start_time < end_time:
            if start_time <= target_time < end_time:
                return slot
        # Wrap-around case (start after end, e.g., 21:00-01:00)
        else:
            # Check if time is after start OR before end (crossing midnight)
            if target_time >= start_time or target_time < end_time:
                return slot
                
    logger.warning(f"Could not find an active slot for time {target_time.strftime('%H:%M')}. Falling back to content root.")
    # If no slot found (e.g., outside channel broadcast hours), return an empty-ish dict
    return {'name': 'OUT_OF_SLOT', 'filler_xml_name': None}

def get_interstitial_ident_path(channel_config: Dict[str, Any]) -> Optional[str]:
    """Finds a random ident using the global ident XML for mandatory inter-show playback."""
    content_root = channel_config['content_root']
    ident_xml_name = channel_config.get('ident_xml_name')
    
    if ident_xml_name:
        ident_xml_path = os.path.join(content_root, ident_xml_name)
        ident_paths = load_video_paths_from_xml(ident_xml_path)
        if ident_paths:
            return random.choice(ident_paths)
            
    logger.warning("Mandatory ident skipped as no ident file was found/contained videos.")
    return None

def get_filler_video_path(gap_duration_seconds: int, channel_config: Dict[str, Any]) -> Optional[str]:
    """
    Selects a short ident for small gaps, or a slot-specific regular filler for longer gaps.
    The slot-specific filler is now responsible for containing either promos or adverts based on the slot.
    """
    content_root = channel_config['content_root']
    now_time = datetime.now().time()
    active_slot = get_active_slot_config(now_time, channel_config)

    # Configuration paths
    ident_xml_name = channel_config.get('ident_xml_name')
    slot_filler_xml_name = active_slot.get('filler_xml_name')
    slot_name = active_slot.get('name', 'N/A')
    
    # 1. Check for Ident Priority (Short Gap <= 90 seconds)
    if gap_duration_seconds <= IDENT_MAX_DURATION_SECONDS and ident_xml_name:
        ident_xml_path = os.path.join(content_root, ident_xml_name)
        ident_paths = load_video_paths_from_xml(ident_xml_path)
        
        if ident_paths:
            logger.info(f"Gap is short ({gap_duration_seconds}s). Selecting random IDENT from {ident_xml_name}.")
            return random.choice(ident_paths)

    # 2. Fallback to Slot-Specific Regular Filler (For all other gaps, including long ones)
    if slot_filler_xml_name:
        filler_xml_path = os.path.join(content_root, slot_filler_xml_name)
        filler_paths = load_video_paths_from_xml(filler_xml_path)

        if filler_paths:
            content_type = "SLOT-SPECIFIC FILLER"
            if gap_duration_seconds > IDENT_MAX_DURATION_SECONDS:
                logger.info(f"Gap is long ({gap_duration_seconds}s). Selecting {content_type} (expected advert/promo) from {slot_filler_xml_name} (Slot: {slot_name}).")
            else:
                 # This only happens if a short gap was detected but no global ident was available.
                 logger.warning(f"Gap is short, but no idents found/configured. Using {content_type} as fallback.")

            return random.choice(filler_paths)

    logger.warning("No suitable filler or ident found for this gap/slot.")
    return None

def load_schedule_for_channel(channel_name, target_date):
    """
    Loads and validates the schedule from the JSON file using the provided structure.
    """
    # File path format: schedule_data/itv_2025-10-09_schedule.json
    schedule_file = os.path.join(SCHEDULE_DIR, f"{channel_name}_{target_date.strftime('%Y-%m-%d')}_schedule.json")
    segments = []
    
    try:
        with open(schedule_file, 'r') as f:
            raw_schedule = json.load(f)

        for item in raw_schedule:
            # 1. Reconstruct datetime from ISO format string (e.g., "2025-10-09T07:00:00")
            start_dt = datetime.fromisoformat(item['start_time'])
            
            # 2. Extract core data
            duration_total = item['slot_duration_total'] # total slot time in seconds
            
            # Use path from video_data, which contains the full URL or relative path
            file_name = item['video_data']['path'] 
            
            # Use content_root from the JSON item
            content_root = item['content_root'] 

            segments.append({
                'start_dt': start_dt,
                'duration': duration_total, # Duration is already in seconds
                'file': file_name,
                'type': 'MAIN', # Assuming all scheduled video items are MAIN content
                'start_time_str': start_dt.strftime('%H%M'), 
                'content_root': content_root
            })
            
        logger.info(f"Loaded {len(segments)} valid program segments for {channel_name} on {target_date.strftime('%Y-%m-%d')}.")
        return segments
        
    except FileNotFoundError:
        logger.error(f"Schedule file not found: {schedule_file}")
        return []
    except Exception as e:
        logger.error(f"Error loading or parsing schedule JSON: {e}")
        return []

def play_video(file_path, base_url, offset_minutes, max_run_minutes, enforce_max_run=True):
    """
    Plays a video using VLC.
    
    :param enforce_max_run: If False (used for MAIN content), the video runs to completion.
    """
    
    # 1. Determine Full Path/URL (Ensure URL encoding for remote paths)
    if file_path.lower().startswith(('http://', 'https://')):
        # This is a remote URL. We must quote (URL encode) any special characters (like spaces) 
        # while keeping the slashes, colons, and basic URL structure intact.
        full_path = quote(file_path, safe='/:?=&') 
    elif file_path.lower().startswith('/'):
        # Absolute local path
        full_path = file_path
    else:
        # Relative local path - join with base_url and quote file name
        quoted_file_path = quote(file_path, safe='/:')
        full_path = os.path.join(base_url, quoted_file_path).replace("\\", "/")

    # 2. Start Time Calculation
    offset_seconds = int(offset_minutes * 60)
    
    # 3. Build VLC command
    vlc_command = [
        'cvlc',
        '--no-video-title-show',
        '--play-and-exit',
        '--no-loop',
        '--fullscreen',
        '--network-caching', '1000', 
        '--http-reconnect',
        '--no-skip-frames',
        '--http-user-agent', 'Mozilla/5.0 (compatible; TVPlayer/1.0)',
    ]
    
    if offset_seconds > 0:
        vlc_command.extend(['--start-time', str(offset_seconds)])
    
    # Append the determined full_path/URL
    vlc_command.append(full_path)
    
    max_run_seconds = int(max_run_minutes * 60)
    start_time_real = time.time()

    # 4. Log the command and start playing
    logger.info(f"PLAYING: {full_path}. Offset: {offset_minutes:.2f}m, Max Run: {max_run_minutes:.2f}m. Enforce Kill: {enforce_max_run}.")
    logger.info(f"VLC COMMAND: {' '.join(vlc_command)}")
    
    vlc_process = subprocess.Popen(vlc_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # 5. Wait for the process to finish, with or without timeout
    try:
        if enforce_max_run:
            # Enforce max run time (used for fillers/ads/idents/short interstitials)
            vlc_process.wait(timeout=max_run_seconds)
            result_code = vlc_process.returncode
            logger.info(f"VLC process exited naturally with code {result_code}.")
        else:
            # Allow video to run to completion (used for main programs)
            logger.info("Timeout disabled. Allowing video to run to completion.")
            vlc_process.wait()
            result_code = vlc_process.returncode
            logger.info(f"VLC process exited naturally with code {result_code}.")

    except subprocess.TimeoutExpired:
        # This block ONLY executes if enforce_max_run was True 
        if enforce_max_run:
            actual_runtime = (time.time() - start_time_real) / 60.0
            logger.warning(f"VLC process timed out after {actual_runtime:.2f}m. Forcefully terminating process to maintain schedule.")
            vlc_process.terminate()
            vlc_process.wait()
            result_code = 1 
        else:
            logger.error("Unexpected TimeoutExpired error when timeout was disabled.")
            result_code = 1
            
    except Exception as e:
        logger.error(f"An error occurred while running VLC: {e}")
        result_code = 1

    finally:
        if vlc_process.poll() is None:
            vlc_process.terminate()
            vlc_process.wait(timeout=5)
            logger.warning("VLC process cleanup complete.")

    actual_runtime = (time.time() - start_time_real) / 60.0
    logger.info(f"Video finished. Actual run time: {actual_runtime:.2f}m, VLC exit code: {result_code}")
    return actual_runtime, result_code

def run_channel_day(channel_name):
    """The core scheduling logic for playing a day's worth of content, now including gap filling and mandatory idents."""
    
    now = datetime.now() # 'now' is our internal clock, tracking the scheduled progress
    today = now.date()
    current_time = now.time()
    
    # --- Load Channel Config ---
    channel_config = load_channel_config(channel_name)
    if not channel_config:
        logger.error(f"Could not load config for channel '{channel_name}'. Cannot run.")
        return
    # --------------------------------

    segments = load_schedule_for_channel(channel_name, today)
    if not segments:
        logger.error("No schedule loaded. Exiting.")
        return

    # Find the current segment index
    start_index = 0
    for i, segment in enumerate(segments):
        # We compare the time component of the expected start datetime
        if segment['start_dt'].time() <= current_time:
            start_index = i
        else:
            break

    # Determine the name for logging
    show_name = segments[start_index]['file'].split('/')[-1]
    logger.info(f"Scheduler starting at index {start_index} ({show_name})")
    
    # --- Main Scheduling Loop ---
    for i in range(start_index, len(segments)):
        segment = segments[i]
        expected_start_dt = segment['start_dt']
        is_first_segment = (i == start_index) # Flag for the very first show played today

        
        # Calculate how late we are (or early, if negative)
        time_diff = now - expected_start_dt
        late_seconds = time_diff.total_seconds()
        late_minutes = late_seconds / 60.0
        
        # --- Gap Filling Logic (Only applies if we are early/on-time) ---
        if late_seconds < 0:
            # We are early (a GAP exists). Fill the gap.
            gap_seconds = -late_seconds
            gap_minutes = gap_seconds / 60.0
            
            logger.info(f"SCHEDULE GAP DETECTED: {gap_seconds:.2f} seconds.")
            
            # --- Select and Play Filler/Advert/Ident to fill the gap ---
            filler_path = get_filler_video_path(
                gap_duration_seconds=int(gap_seconds), 
                channel_config=channel_config
            )

            if filler_path:
                # Filler MUST be cut off at the exact scheduled start time
                
                # We need to know the *exact* real time the filler started
                real_start_dt = datetime.now()
                
                actual_filler_runtime, exit_code = play_video(
                    file_path=filler_path,
                    base_url=channel_config['content_root'], 
                    offset_minutes=0.0,
                    max_run_minutes=gap_minutes, # Max run is the duration of the gap
                    enforce_max_run=True # Always kill filler/advert/ident to hit schedule
                )
                
                # --- NEW DRIFT CORRECTION ---
                # Calculate the exact real time the filler *should* have ended
                filler_expected_end_dt = real_start_dt + timedelta(minutes=gap_minutes)
                
                # The real time now
                real_end_dt = datetime.now()
                
                # Check if the filler ran short (due to crash or short file length)
                time_to_sleep = (filler_expected_end_dt - real_end_dt).total_seconds()

                if time_to_sleep > 0.0:
                    logger.warning(f"FILLER CRASHED/RAN SHORT ({actual_filler_runtime:.2f}m). Remaining gap to fill: {time_to_sleep:.2f} seconds. Waiting to maintain schedule.")
                    time.sleep(time_to_sleep)
                
                # Advance internal clock 'now' to the expected start of the main show
                now = expected_start_dt 
                
            else:
                # If no filler/ident was found, simply wait for the remainder of the gap.
                logger.warning(f"No filler found for gap. Waiting {gap_seconds:.2f} seconds.")
                time.sleep(gap_seconds)
                now = expected_start_dt # Time is now exactly on schedule
                
            # Recalculate late_minutes for the MAIN segment start. Should be 0.0 now.
            late_minutes = (now - expected_start_dt).total_seconds() / 60.0

        
        # --- Pre-Slot Logging ---
        current_show_name = segment['file'].split('/')[-1] 
        logger.info(f"\n--- START SLOT: {current_show_name} (Type: {segment['type']}) ---")
        
        # --- Mandatory Interstitial Ident Logic (Only for segments *after* the first one) ---
        if segment['type'] == 'MAIN' and not is_first_segment:
            ident_path = get_interstitial_ident_path(channel_config)
            
            if ident_path:
                ident_duration_minutes = MANDATORY_IDENT_MAX_SECONDS / 60.0 
                
                logger.info("MANDATORY IDENT: Playing short ident between MAIN shows.")
                
                # --- NEW REAL-TIME TRACKING FOR IDENT ---
                real_start_dt = datetime.now()
                ident_expected_end_dt = real_start_dt + timedelta(seconds=MANDATORY_IDENT_MAX_SECONDS)
                
                # Play the ident, enforcing max run time to 15s in case the video file is long.
                actual_ident_runtime, ident_exit_code = play_video(
                    file_path=ident_path,
                    base_url=channel_config['content_root'],
                    offset_minutes=0.0,
                    max_run_minutes=ident_duration_minutes, 
                    enforce_max_run=True 
                )
                
                real_end_dt = datetime.now()

                # Check if the ident ran short
                time_to_sleep = (ident_expected_end_dt - real_end_dt).total_seconds()

                if time_to_sleep > 0.0:
                    logger.warning(f"IDENT CRASHED/RAN SHORT ({actual_ident_runtime:.2f}m). Remaining gap to fill: {time_to_sleep:.2f} seconds. Waiting to maintain schedule.")
                    time.sleep(time_to_sleep)

                # Internal clock 'now' must be advanced by the full Ident max duration (15s)
                now += timedelta(seconds=MANDATORY_IDENT_MAX_SECONDS)
                
                # Recalculate late_minutes for MAIN segment start
                late_minutes = (now - expected_start_dt).total_seconds() / 60.0
                
                if late_minutes > 0:
                    logger.info(f"SCHEDULE DRIFT: Mandatory ident successfully added {late_minutes:.2f}m of lateness before MAIN show.")
            else:
                logger.warning("Mandatory ident skipped as no ident file was found/contained videos.")
        
        # --- MAIN Segment Playback Logic ---
        jump_in_offset_m = 0.0
        max_run_minutes = segment['duration'] / 60.0
        segment_duration_m = segment['duration'] / 60.0 # Pre-calculate full duration
        enforce_catch_up = False

        if late_minutes > 0:
            # We are late. Decide whether to catch up or drift.
            
            if segment['type'] != 'MAIN':
                # NON-MAIN (e.g., ad blocks) must always catch up.
                enforce_catch_up = True
                logger.warning(f"LATE: Scheduler is running {late_minutes:.2f} minutes late for this non-MAIN slot (Catch-up mode).")
            
            elif is_first_segment:
                # FIRST MAIN segment MUST catch up (as per the user requirement).
                enforce_catch_up = True
                logger.warning(f"LATE: Scheduler is running {late_minutes:.2f} minutes late for FIRST MAIN slot. Applying jump-in offset to catch up.")
            
            else:
                # SUBSEQUENT MAIN segments always start from 0:00 (drift mode - user requirement).
                logger.warning(f"LATE: Scheduler is running {late_minutes:.2f} minutes late for this SUBSEQUENT MAIN slot. Starting from the beginning (0:00 offset).")
                jump_in_offset_m = 0.0
                enforce_catch_up = False # Explicitly clear catch up for drift mode


            # If we need to catch up, apply the offset and adjust run time.
            if enforce_catch_up:
                jump_in_offset_m = late_minutes
                remaining_segment_m = segment_duration_m - jump_in_offset_m
                
                if remaining_segment_m <= 0:
                    logger.warning("JUMP-IN EXCEEDS DURATION: Skipping segment entirely.")
                    # Advance 'now' to the expected end time of the skipped slot
                    now = expected_start_dt + timedelta(seconds=segment['duration'])
                    continue 
                
                max_run_minutes = remaining_segment_m
                
                logger.warning(f"RUN: Calculated jump-in offset of {jump_in_offset_m:.2f}m. New max run time adjusted to {max_run_minutes:.2f}m.")


        # --- Play Video ---
        
        # MAIN content runs to completion (False) UNLESS we are in catch-up mode 
        # (first segment or non-MAIN content), where we need to enforce the max_run_minutes cutoff.
        enforce_kill = (segment['type'] != 'MAIN' or enforce_catch_up)
        
        # Use the content root from the loaded JSON segment
        content_root = segment['content_root']
        logger.info(f"Segment Content Root: {content_root}")

        actual_runtime, exit_code = play_video(
            file_path=segment['file'],
            base_url=content_root,
            offset_minutes=jump_in_offset_m,
            max_run_minutes=max_run_minutes,
            enforce_max_run=enforce_kill
        )

        # Update the 'now' time based on the actual duration the video ran
        # This keeps the internal clock advanced by the actual time passed.
        now += timedelta(minutes=actual_runtime)
        logger.info(f"Show finished. Current time advanced to {now.strftime('%H:%M:%S')}")


    logger.info("End of channel schedule for today.")


def main_loop():
    """Starts the channel player, reading channel name from command line arguments."""
    channel = DEFAULT_CHANNEL
    
    # Check if a command line argument was provided (the script name is sys.argv[0])
    if len(sys.argv) > 1:
        # Use the first argument as the channel name
        channel = sys.argv[1].lower()
        logger.info(f"Overriding default channel. Using channel from command line: '{channel}'")

    log_file = setup_logging(channel)
    logger.info(f"Starting player with channel: '{channel}'.")
    
    today = date.today() 
    
    logger.info(f"--- STARTING CHANNEL RUN: '{channel}' on {today.strftime('%Y-%m-%d')} (Mode: LIVE) ---")
    
    try:
        run_channel_day(channel)
    except KeyboardInterrupt:
        logger.info("Player stopped by user.")
    except Exception as e:
        logger.error(f"A fatal error occurred: {e}", exc_info=True)


if __name__ == "__main__":
    main_loop()
