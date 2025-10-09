import sys
import os
import subprocess
import time
import datetime
import logging
import json
import xml.etree.ElementTree as ET
import urllib.parse 

# Global paths and settings
# CONFIG_DIR is the root of the project (e.g., /home/markd/raspberry_pi_TV/)
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(CONFIG_DIR, 'logs')
SCHEDULE_DIR = os.path.join(CONFIG_DIR, 'schedule_data')
CHANNELS_DIR = os.path.join(CONFIG_DIR, 'channel_configs')
LAST_STATE_FILE = os.path.join(CONFIG_DIR, 'last_channel_state.txt')

# --- Scheduling Constant ---
# If the calculated max run time for the FIRST LATE SLOT is less than this, the video is skipped.
MIN_PLAYBACK_TIME_REQUIRED = 5.0 # seconds
# -----------------------------

# Global to track the currently running VLC process for cleanup on exit
current_vlc_process = None

# --- Logging Setup ---

def setup_logging():
    """Initializes logging configuration."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_filename = os.path.join(LOG_DIR, f"{timestamp}_tvplayer.log")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(funcName)s: %(message)s',
        handlers=[
            logging.FileHandler(log_filename, mode='a', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.info(f"Logging initialized. Output saved to: {log_filename}")

# --- State Management ---

def load_current_channel_state():
    """Loads the last saved channel name."""
    try:
        with open(LAST_STATE_FILE, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

def save_current_channel_state(channel_name):
    """Saves the current channel name."""
    try:
        with open(LAST_STATE_FILE, 'w') as f:
            f.write(channel_name)
        logging.debug(f"Updated channel state to: {channel_name}")
    except Exception as e:
        logging.error(f"Could not save channel state: {e}")

# --- Channel Configuration (XML) Loading ---

def load_channel_metadata(channel_name):
    """Loads content_root and filler_xml path from the channel's XML config file."""
    config_file = os.path.join(CHANNELS_DIR, f"{channel_name}_channel.xml")
    if not os.path.exists(config_file):
        logging.critical(f"Channel configuration file not found: {config_file}")
        return None

    try:
        tree = ET.parse(config_file)
        root = tree.getroot()
        
        content_root = root.get('content_root')
        
        # We still extract this for fallback, but the JSON path is preferred later
        first_slot = root.find('slot')
        filler_xml_rel_path = first_slot.get('filler_xml') if first_slot is not None else None

        if not content_root:
            logging.error(f"Missing 'content_root' attribute in {config_file}. Cannot run channel.")
            return None
            
        return {
            'content_root': content_root,
            'filler_xml_rel_path': filler_xml_rel_path
        }

    except ET.ParseError as e:
        logging.critical(f"Error parsing XML channel config {config_file}: {e}")
        return None
    except Exception as e:
        logging.critical(f"Unexpected error loading channel metadata: {e}")
        return None

# --- Filler Content (XML) Manifest Loading ---

def load_filler_manifest(channel_name, content_root, filler_xml_rel_path):
    """
    Loads the filler manifest (ad/ident list) from an XML file, trying multiple 
    common locations relative to the project structure and the content root.
    """
    
    if not filler_xml_rel_path:
        logging.warning("No filler XML path provided. Returning empty filler list.")
        return []

    filler_manifest_path = None
    
    # Attempt 1: Relative to the channel's Content Root 
    temp_path = os.path.join(content_root, filler_xml_rel_path)
    if os.path.exists(temp_path):
        filler_manifest_path = temp_path
        
    # Attempt 2: Relative to the channel's config subdirectory
    if filler_manifest_path is None:
        temp_path = os.path.join(CHANNELS_DIR, channel_name, filler_xml_rel_path)
        if os.path.exists(temp_path):
            filler_manifest_path = temp_path
        
    # Attempt 3: Relative to the main channel config directory
    if filler_manifest_path is None:
        temp_path = os.path.join(CHANNELS_DIR, filler_xml_rel_path)
        if os.path.exists(temp_path):
            filler_manifest_path = temp_path
        
    # Attempt 4: Relative to the project root (CONFIG_DIR)
    if filler_manifest_path is None:
        temp_path = os.path.join(CONFIG_DIR, filler_xml_rel_path)
        if os.path.exists(temp_path):
            filler_manifest_path = temp_path
        
    # --- Final Check and Loading ---
    if filler_manifest_path is None:
        logging.critical(f"Filler XML manifest not found after all attempts. Last path checked (CONFIG_DIR attempt): {os.path.join(CONFIG_DIR, filler_xml_rel_path)}")
        return []

    logging.info(f"Loading filler manifest from: {filler_manifest_path}")

    try:
        tree = ET.parse(filler_manifest_path)
        root = tree.getroot()
        filler_items = []
        
        # 1. Try to load the expected 'video' elements (path and duration as attributes)
        for video_element in root.findall('video'): 
            path = video_element.get('path')
            duration_str = video_element.get('duration')
            remote = video_element.get('remote')
            title = video_element.get('title', os.path.basename(path) if path else 'Unknown Filler')

            if path and duration_str:
                try:
                    duration = float(duration_str)
                    filler_items.append({
                        'path': path,
                        'title': title,
                        'duration': duration,
                        'remote': str(remote).lower() == 'true'
                    })
                except ValueError:
                    logging.warning(f"Could not parse duration for video element: {path}. Skipping.")

        # 2. Try to load the 'file' elements (path in 'name', duration in child 'length')
        for file_element in root.findall('file'): 
            path = file_element.get('name')
            length_element = file_element.find('length')
            duration = None
            
            if length_element is not None and length_element.text:
                try:
                    duration = float(length_element.text)
                except ValueError:
                    logging.warning(f"Could not parse duration for file element: {path}. Skipping.")
                    continue

            # Determine title: Use the filename from the path if available
            title = os.path.basename(path) if path else 'Unknown Filler'
            remote = True 

            if path and duration is not None:
                # Only append if not already added through the 'video' block (though this is unlikely)
                if not any(item['path'] == path for item in filler_items):
                    filler_items.append({
                        'path': path,
                        'title': title,
                        'duration': duration,
                        'remote': remote
                    })


        logging.info(f"Loaded {len(filler_items)} filler items from XML.")
        return filler_items
        
    except ET.ParseError as e:
        logging.error(f"Error parsing XML filler manifest {filler_manifest_path}: {e}")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred while loading XML filler manifest: {e}")
        return []


# --- Schedule Loading and Parsing (JSON) ---

def load_schedule_for_channel(channel_name, date):
    """
    Loads the schedule from a JSON file, extracts the schedule list, and the 
    filler path from the first segment.
    Returns (validated_schedule_list, filler_xml_path_from_json)
    """
    schedule_file = os.path.join(SCHEDULE_DIR, f"{channel_name}_{date.strftime('%Y-%m-%d')}_schedule.json")
    
    if not os.path.exists(schedule_file):
        logging.error(f"Schedule file not found for {channel_name} on {date.strftime('%Y-%m-%d')}: {schedule_file}")
        return [], None # Return empty list and None path

    try:
        with open(schedule_file, 'r', encoding='utf-8') as f:
            schedule_data = json.load(f)
        
        if not isinstance(schedule_data, list):
            logging.error(f"JSON schedule is not a list in {schedule_file}.")
            return [], None

        validated_schedule = []
        required_raw_keys = ['start_time', 'slot_duration_total', 'video_data', 'show_name']
        
        # NEW: Extract filler path from the first segment (if it exists)
        filler_xml_path_from_json = schedule_data[0].get('filler_xml_path') if schedule_data else None

        for segment in schedule_data:
            # 1. Check for mandatory raw keys
            if not all(key in segment for key in required_raw_keys):
                missing_keys = [key for key in required_raw_keys if key not in segment]
                logging.error(f"Schedule segment is missing mandatory keys: {missing_keys}. Skipping invalid segment: {segment}")
                continue
            
            video_data = segment['video_data']
            
            # 2. Extract and safely convert video path and duration
            if 'path' not in video_data:
                logging.error(f"Schedule segment is missing 'video_data.path'. Skipping invalid segment: {segment}")
                continue
                
            raw_duration = video_data.get('duration')
            duration = None
            if raw_duration is not None:
                 try:
                     duration = float(raw_duration)
                 except (ValueError, TypeError):
                     logging.warning(f"Invalid duration value for program '{segment['show_name']}'. Setting to None.")
            
            # 3. Calculate start and end times (as time strings)
            try:
                # Parse the ISO timestamp from the schedule file
                start_dt = datetime.datetime.fromisoformat(segment['start_time'])
                
                # Calculate end time based on total slot duration
                slot_duration_sec = float(segment['slot_duration_total'])
                end_dt = start_dt + datetime.timedelta(seconds=slot_duration_sec)
                
                # Extract simple time strings (HH:MM:SS) for the rest of the script logic
                start_time_str = start_dt.strftime('%H:%M:%S')
                end_time_str = end_dt.strftime('%H:%M:%S')
                
            except Exception as e:
                logging.error(f"Failed to parse time or duration for segment '{segment['show_name']}'. Error: {e}. Skipping.")
                continue

            # 4. Determine segment type (MAIN vs. MISC/FILLER)
            segment_type = 'MAIN'
            show_name_upper = segment['show_name'].upper()
            if show_name_upper == 'MISC' or show_name_upper == 'FILLER':
                segment_type = show_name_upper
            
            # 5. Construct the new, simplified segment structure
            validated_segment = {
                'start': start_time_str,
                'end': end_time_str,
                'path': video_data['path'],
                'duration': duration, # Used for jump-in/offset calculation
                'title': segment['show_name'],
                'type': segment_type, 
                # Determine remote status: use explicit key if present, otherwise guess based on '://' in path.
                'remote': str(segment.get('remote', '://' in video_data['path'])).lower() == 'true',
            }
            
            validated_schedule.append(validated_segment)

        logging.info(f"Loaded {len(validated_schedule)} valid program segments for {channel_name} on {date.strftime('%Y-%m-%d')}.")
        return validated_schedule, filler_xml_path_from_json
        
    except json.JSONDecodeError as e:
        logging.error(f"Error parsing JSON schedule file {schedule_file}: {e}")
        return [], None
    except Exception as e:
        logging.error(f"An unexpected error occurred while loading schedule: {e}")
        return [], None

# --- Video Playback ---
def play_video(file_path, is_remote, offset_sec=0.0, max_run_sec=None):
    """Plays a video using cvlc."""
    global current_vlc_process

    # --- FIX: URL-encode the path if it is remote (prevents issue with spaces) ---
    if is_remote:
        # Quote the path component of the URL, but only if it's not already encoded
        parsed_url = urllib.parse.urlsplit(file_path)
        encoded_path = urllib.parse.quote(parsed_url.path, safe='/:') # Encode path, but keep slashes and colons (for protocol)
        
        # Rebuild the URL with the encoded path
        if encoded_path != parsed_url.path:
            file_path = parsed_url._replace(path=encoded_path).geturl()
    # -----------------------------------------------

    command = [
        'cvlc',
        '--no-video-title-show',
        '--play-and-exit',
        '--no-loop',
        '--fullscreen',
    ]
    
    if is_remote:
        command.extend([
            # FIX 1: Reduced cache to 1s (1000ms) for quicker connection/less stall
            '--network-caching', '1000', 
            '--http-reconnect',
            '--no-skip-frames',
            # FIX 2: Set a User-Agent to prevent server rejection of stream requests
            '--http-user-agent', 'Mozilla/5.0 (compatible; TVPlayer/1.0)' 
        ])
    
    if offset_sec > 0:
        # Use simple string formatting for start time to keep it clean for logging
        command.append(f'--start-time={offset_sec:.2f}')
    
    # The file_path is now added, which is either the local path or the encoded remote URL
    command.append(file_path)

    # Convert max_run_sec and offset_sec for logging output to minutes
    if max_run_sec is not None:
        log_max_run = f"{max_run_sec / 60.0:.2f}m" 
    else:
        log_max_run = "full duration"
        
    log_offset = offset_sec / 60.0
    
    logging.info(f"PLAYING: {os.path.basename(file_path)} (Remote: {is_remote}). Offset: {log_offset:.2f}m, Max Run: {log_max_run}.")

    # --- Log the command before execution ---
    command_str = " ".join(command)
    logging.info(f"VLC COMMAND: {command_str}")
    # -------------------------------------------
    
    try:
        # We use preexec_fn=os.setsid to put cvlc in a new process group.
        # This is CRITICAL for being able to kill the entire group later.
        proc = subprocess.Popen(command, preexec_fn=os.setsid, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        current_vlc_process = proc # Store the process object globally
    except FileNotFoundError:
        logging.critical("VLC (cvlc) command not found. Please ensure VLC is installed and in your PATH.")
        return (-99, 0.0)

    
    start_time = time.time()
    
    if max_run_sec is not None and max_run_sec > 0:
        try:
            # We still add a 5-second buffer to the wait time to give the process a chance to clean up,
            # but the max run time itself is strictly enforced by the logic above.
            wait_time = max_run_sec + 5
            proc.wait(timeout=wait_time)
            
            actual_runtime = time.time() - start_time
            return (proc.returncode, actual_runtime)
        
        except subprocess.TimeoutExpired:
            actual_runtime = time.time() - start_time
            # Report timeout duration in minutes
            logging.warning(f"VLC process timed out after {actual_runtime / 60.0:.2f}m. Forcefully terminating process to maintain schedule.")
            
            try:
                # Use os.killpg to kill the entire process group
                os.killpg(os.getpgid(proc.pid), 9)
            except Exception as e:
                logging.error(f"Failed to kill VLC process: {e}")
            
            return (0, actual_runtime) 
    
    else:
        try:
            proc.wait()
            return (-1, time.time() - start_time)
        except Exception as e:
            logging.error(f"Error waiting for VLC process: {e}")
            return (-1, time.time() - start_time)

# --- Filler Content (Breaks/Adverts) Execution ---
def run_filler_break(filler_manifest, duration_sec):
    """Plays items from the filler manifest sequentially until the duration is met."""
    if not filler_manifest:
        logging.warning("FILLER: No filler items available. Skipping break.")
        time.sleep(duration_sec)
        return

    # Report break duration in minutes
    logging.info(f"FILLER: Starting break for {duration_sec / 60.0:.2f}m (Items: {len(filler_manifest)})")

    start_time = time.time()
    elapsed_time = 0
    filler_index = 0

    while elapsed_time < duration_sec:
        item = filler_manifest[filler_index % len(filler_manifest)]
        time_remaining = duration_sec - elapsed_time
        max_run = min(item['duration'], time_remaining)

        if max_run <= 0.0:
            logging.debug("FILLER: Time remaining is zero or negative. Exiting filler loop.")
            break

        # Check if there's enough time left to play the current filler item
        if max_run < 5.0: # Arbitrary minimum for filler, much lower than main content
             logging.debug(f"FILLER: Skipping '{item['title']}'. Remaining time ({max_run:.2f}s) too short.")
             time.sleep(max_run) # Sleep for the remaining small time
             elapsed_time += max_run
             break # Exit the filler loop
             
        # Report item length and max run duration in minutes
        item_length_m = item['duration'] / 60.0
        max_run_m = max_run / 60.0
        logging.debug(f"FILLER: Playing {item['title']} (Length: {item_length_m:.2f}m) for max {max_run_m:.2f}m.")
        
        result_code, actual_play_duration = play_video(item['path'], item['remote'], offset_sec=0.0, max_run_sec=max_run)
        
        elapsed_time += actual_play_duration
        
        if result_code != 0 and result_code != -1: 
            # Report actual play duration in minutes
            actual_play_m = actual_play_duration / 60.0
            logging.info(f"FILLER: Video '{item['title']}' exited with non-zero code {result_code}. Time counted: {actual_play_m:.2f}m.")

        filler_index += 1
        
    # Report total elapsed time in minutes
    logging.info(f"FILLER: Break finished. Total elapsed time: {elapsed_time / 60.0:.2f}m.")


# --- Main Scheduling Logic ---
def find_current_slot_index(schedule, today_dt):
    """
    Finds the index of the current or next slot to play.
    Uses the simple 'start' and 'end' time strings (HH:MM:SS) generated by the loader.
    """
    for i, slot in enumerate(schedule):
        slot_start_str = slot['start']
        
        # Use today's date with the slot's time string
        slot_start_dt = datetime.datetime.strptime(f"{today_dt.strftime('%Y-%m-%d')} {slot_start_str}", '%Y-%m-%d %H:%M:%S')

        if today_dt >= slot_start_dt:
            slot_end_str = slot['end']
            slot_end_dt = datetime.datetime.strptime(f"{today_dt.strftime('%Y-%m-%d')} {slot_end_str}", '%Y-%m-%d %H:%M:%S')
            
            # Handle time crossover (e.g., 23:00 to 01:00)
            if slot_end_dt < slot_start_dt:
                slot_end_dt += datetime.timedelta(days=1)
            
            if today_dt < slot_end_dt:
                return i, True 
            
        else:
            return i, False
            
    logging.info("All scheduled slots for today have finished.")
    return -1, False

def run_channel_day(channel_name, schedule, content_root, filler_manifest):
    """Main scheduling loop for a single day of a channel."""
    SUCCESS_TOLERANCE = 5.0 
    global MIN_PLAYBACK_TIME_REQUIRED

    today_dt = datetime.datetime.now()
    is_first_slot_of_run = True

    start_index, is_late_start = find_current_slot_index(schedule, today_dt)
    
    if start_index == -1:
        logging.info("Schedule is empty or already completed for the day. Exiting run.")
        return

    logging.info(f"Scheduler starting at index {start_index} ({schedule[start_index]['title']})")
    logging.info(f"Channel Content Root set to: {content_root}")

    for i in range(start_index, len(schedule)):
        slot = schedule[i]
        
        # Re-construct start/end datetimes using the simple time strings
        slot_date = datetime.date.today()
        slot_start_dt = datetime.datetime.strptime(f"{slot_date.strftime('%Y-%m-%d')} {slot['start']}", '%Y-%m-%d %H:%M:%S')
        slot_end_dt = datetime.datetime.strptime(f"{slot_date.strftime('%Y-%m-%d')} {slot['end']}", '%Y-%m-%d %H:%M:%S')
        
        # Handle midnight crossover for end time
        if slot_end_dt < slot_start_dt:
            slot_end_dt += datetime.timedelta(days=1)
        
        current_time_dt = datetime.datetime.now()
        wait_time = (slot_start_dt - current_time_dt).total_seconds()
        
        if wait_time > 1.0: 
            # Report wait time in minutes
            logging.info(f"Waiting for next slot to start: {slot['title']} at {slot['start']}. Sleeping for {wait_time / 60.0:.2f}m...")
            time.sleep(wait_time)
            current_time_dt = datetime.datetime.now()
            
        logging.info(f"\n--- START SLOT: {slot['title']} (Type: {slot['type']}) ---")

        time_elapsed_in_slot = (current_time_dt - slot_start_dt).total_seconds()
        time_to_slot_end = (slot_end_dt - current_time_dt).total_seconds()

        if time_to_slot_end <= 0:
            logging.warning(f"Slot '{slot['title']}' already ended. Skipping.")
            is_first_slot_of_run = False
            continue
            
        video_offset = 0.0
        max_run_time = time_to_slot_end
        resolved_file_path = slot['path']
        
        # Initialize result variables outside the conditional blocks
        result_code = -1 # Default code if execution is skipped or failed
        actual_play_duration = 0.0

        # --- Late Start / Jump-in Logic ---
        if is_first_slot_of_run and time_elapsed_in_slot > 0:
            # Report late time in minutes
            logging.warning(f"LATE: Scheduler is running {time_elapsed_in_slot / 60.0:.2f} minutes late for this slot.")

            video_full_duration = slot.get('duration')
            
            if video_full_duration is not None and video_full_duration > 0:
                video_offset = time_elapsed_in_slot % video_full_duration
                remaining_video_time = video_full_duration - video_offset
                max_run_time = min(remaining_video_time, time_to_slot_end)
                
                # Report offset and max run in minutes
                log_offset_m = video_offset / 60.0
                log_max_run_m = max_run_time / 60.0
                logging.warning(f"FIRST RUN: Calculated jump-in offset of {log_offset_m:.2f}m. New max run time adjusted to {log_max_run_m:.2f}m.")
                
            else:
                logging.warning("Video duration data is missing/invalid. Cannot calculate jump-in offset. Starting from 0 for the remainder of the slot.")
                video_offset = 0.0
                max_run_time = time_to_slot_end
                
            is_first_slot_of_run = False
        
        # --- EXECUTION: MAIN or MISC Playback ---
        if slot['type'] == 'MAIN' or slot['type'] == 'MISC':
            
            if time_to_slot_end <= 0:
                continue
            
            is_local_path_issue = False
            
            # --- CRITICAL PRE-EMPTIVE SKIP CHECK ---
            # If we calculated a very small run time for a late slot, skip it to avoid VLC overhead failure.
            if slot['remote'] and max_run_time < MIN_PLAYBACK_TIME_REQUIRED:
                logging.warning(f"SKIP: Remaining slot time ({max_run_time:.2f}s) is less than the required minimum startup time ({MIN_PLAYBACK_TIME_REQUIRED}s). Skipping remote video.")
                result_code = -101 # Code for pre-emptive skip due to time constraint
            
            # --- LOCAL FILE PATH CHECK ---
            elif not slot['remote']:
                 # 1. Resolve local path
                 if not os.path.isabs(slot['path']):
                     resolved_file_path = os.path.join(content_root, slot['path'])
                 else:
                     resolved_file_path = slot['path']
                 
                 # 2. Check if local file exists
                 if not os.path.exists(resolved_file_path):
                     logging.error(f"LOCAL FILE NOT FOUND: Cannot find '{resolved_file_path}'. Skipping video and running filler.")
                     is_local_path_issue = True
                     result_code = -100 # Code for missing local file
                     actual_play_duration = 0.0
                 
            # --- VIDEO PLAYBACK (Only if not skipped by either check) ---
            if result_code not in [-100, -101]:
                file_to_play = resolved_file_path if not slot['remote'] else slot['path']
                result_code, actual_play_duration = play_video(file_to_play, slot['remote'], offset_sec=video_offset, max_run_sec=max_run_time)

            
            # 4. Handle success/failure after playback attempt
            # A slot is considered successful if the player ran for almost the full max_run_time
            is_scheduled_success = (max_run_time > 0 and actual_play_duration >= max_run_time - SUCCESS_TOLERANCE)
            
            if result_code == 0 or is_scheduled_success:
                if result_code != 0 and is_scheduled_success:
                    # Report actual play duration in minutes
                    actual_play_m = actual_play_duration / 60.0
                    logging.info(f"PLAYBACK GRACE: VLC exited with non-zero code {result_code} but played for {actual_play_m:.2f}m. Treating as a scheduled success.")
                pass 
            
            elif result_code != 0:
                # Video failed or didn't run long enough, fill the rest of the slot
                actual_play_m = actual_play_duration / 60.0
                
                if result_code == -100:
                    log_message = f"SHOW SKIPPED (Reason: Missing Local File). Running filler for the remaining time in the slot."
                elif result_code == -101:
                    log_message = f"SHOW SKIPPED (Reason: Time too short for VLC startup). Running filler for the remaining time in the slot."
                else:
                    # Report actual play duration in minutes
                    log_message = f"SHOW FAILED (Code: {result_code}, Duration: {actual_play_m:.2f}m). Running filler for the remaining time in the slot."
                    
                logging.error(log_message)

                time_after_failure = datetime.datetime.now()
                filler_duration_sec = (slot_end_dt - time_after_failure).total_seconds()
                
                if filler_duration_sec > 0:
                    # Report filler duration in minutes
                    logging.info(f"Attempting to fill remaining {filler_duration_sec / 60.0:.2f}m.")
                    run_filler_break(filler_manifest, filler_duration_sec)
                else:
                    logging.warning("Filler skipped as slot has already ended or time remaining is zero.")

        # --- EXECUTION: FILLER Break (Slot designated for filler) ---
        elif slot['type'] == 'FILLER' and time_to_slot_end > 0:
            logging.info("Running scheduled dedicated FILLER slot.")
            run_filler_break(filler_manifest, time_to_slot_end)
            
        else:
            logging.warning(f"Unknown slot type '{slot['type']}' or time remaining is zero. Skipping to next slot.")
            
        is_first_slot_of_run = False

    logging.info("End of schedule reached.")

def terminate_vlc():
    """Gracefully and forcefully stops the currently tracked VLC process and its group."""
    global current_vlc_process
    if current_vlc_process and current_vlc_process.poll() is None:
        try:
            # Get the Process Group ID (PGID) and send a SIGKILL (9) to it
            pgid = os.getpgid(current_vlc_process.pid)
            logging.info(f"CLEANUP: Sending SIGKILL to process group {pgid} (VLC).")
            os.killpg(pgid, 9) 
            current_vlc_process = None
            time.sleep(1) # Wait a moment for termination
        except Exception as e:
            logging.error(f"Error during VLC process group termination: {e}")

def main_loop(channel_name):
    """The continuous main loop that runs the channel."""
    save_current_channel_state(channel_name)
    
    metadata = load_channel_metadata(channel_name)
    if not metadata:
        sys.exit(1)

    content_root = metadata['content_root']
    # Path from the XML config (used as fallback)
    filler_xml_path_from_xml = metadata['filler_xml_rel_path'] 
    
    # Initialize filler manifest outside the inner loop
    filler_manifest = []

    while True:
        today = datetime.date.today()
        
        logging.info(f"--- STARTING CHANNEL RUN: '{channel_name}' on {today.strftime('%Y-%m-%d')} (Mode: LIVE) ---")
        
        # Load schedule AND the filler path defined within the schedule data
        schedule, filler_xml_path_from_json = load_schedule_for_channel(channel_name, today)
        
        if not schedule:
            logging.error(f"Failed to load schedule for {channel_name} or schedule is empty. Retrying in 60 seconds.")
            time.sleep(60)
            continue
            
        # Determine which filler path to use (JSON preferred, XML fallback)
        final_filler_path = filler_xml_path_from_json if filler_xml_path_from_json else filler_xml_path_from_xml

        # Load the filler manifest based on the determined path
        if final_filler_path:
            # NOW PASSING content_root, which is the channel's base media directory
            filler_manifest = load_filler_manifest(channel_name, content_root, final_filler_path)
        else:
            filler_manifest = []
            
        if not filler_manifest:
            logging.warning("No filler content loaded. Ad breaks will be silent sleep periods.")

        run_channel_day(channel_name, schedule, content_root, filler_manifest)
        
        now = datetime.datetime.now()
        if now.date() == today and now.hour < 3:
            logging.info("Schedule completed. Waiting for the next day's schedule (Midnight).")
            tomorrow = today + datetime.timedelta(days=1)
            midnight_next_day = datetime.datetime.combine(tomorrow, datetime.time(0, 0, 5)) 
            wait_time = (midnight_next_day - now).total_seconds()
            
            if wait_time > 0:
                # This wait is typically long (hours), so keeping the output clear
                time.sleep(wait_time)
        else:
            logging.info("Schedule completed during the day. Checking for new schedule in 1 hour.")
            time.sleep(3600)

if __name__ == '__main__':
    setup_logging()
    
    start_channel = 'bbc'
    if len(sys.argv) > 1:
        start_channel = sys.argv[1]
    else:
        last_state = load_current_channel_state()
        if last_state:
            start_channel = last_state
            
    logging.info(f"Starting player with channel: '{start_channel}'.")
            
    try:
        main_loop(start_channel)
    except KeyboardInterrupt:
        logging.info("\nScript terminated by user (Ctrl+C). Initiating cleanup...")
        # CRITICAL: Call the function to kill VLC before exiting
        terminate_vlc() 
        sys.exit(0)
    except Exception as e:
        logging.critical(f"A fatal error occurred in the main loop: {e}", exc_info=True)
        sys.exit(1)
