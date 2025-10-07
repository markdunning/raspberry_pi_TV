import datetime
import json
import subprocess
import time
import os
import logging
from typing import List, Dict, Any, Optional
import urllib.parse 
import xml.etree.ElementTree as ET
import random
import sys
import glob
import signal 
import argparse 

# --- 1. CONFIGURATION & GLOBALS ---

# Base path for content, relative to the script location
BASE_CONTENT_PATH = os.path.dirname(os.path.abspath(__file__))

# Define the log directory path
LOG_DIR = os.path.join(BASE_CONTENT_PATH, 'logs')
# Path for the communication file used by the TV Guide GUI
OVERRIDE_FILE = os.path.join(BASE_CONTENT_PATH, 'override_video.txt')


# Ensure the log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Generate a timestamped log filename
TIMESTAMP = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"{TIMESTAMP}_tvplayer.log")

# UPDATE: Channel list file path
CHANNEL_LIST_FILE = os.path.join(BASE_CONTENT_PATH, 'channel_configs', 'channel_list.json')

# UPDATE: Schedule files are in 'schedule_data'
SCHEDULE_DIR = os.path.join(BASE_CONTENT_PATH, 'schedule_data')

# Ensure the directories exist
os.makedirs(os.path.dirname(CHANNEL_LIST_FILE), exist_ok=True)
os.makedirs(SCHEDULE_DIR, exist_ok=True)

# Minimum duration a video must have to be worth playing, used for late slots.
MIN_PLAYBACK_TIME = 5.0 # seconds

# VLC Configuration (Updated to use cvlc)
VLC_PATH = 'cvlc' # Using command-line VLC
VLC_ARGS = [
    '--no-video-title-show', # Do not show video title
    '--play-and-exit',       # Exit after playback finishes
    '--no-loop',             # Do not loop the video
    '--fullscreen',
]

# Flags specific to remote streaming (network caching and resilience)
REMOTE_STREAMING_FLAGS = [
    '--network-caching', '5000', # Increased cache for resilience
    '--http-reconnect',          # Force re-connect on stream interruption
]

# Global flag for dry-run mode, set by argparse
DRY_RUN = False 
# Global variable to track simulated time progression in dry-run mode
CURRENT_SIMULATED_TIME = datetime.datetime.now().replace(tzinfo=None) 

# Global variable to track the currently running VLC process for graceful exit (Ctrl+C)
VLC_PROCESS: Optional[subprocess.Popen] = None 
# Buffer time for external timeout when playing video.
VLC_TIMEOUT_BUFFER = 5.0 # Seconds buffer added to max_runtime for external timeout check

# Global variable to signal an override interruption from play_video
OVERRIDE_INTERRUPTED = -1.0 # Sentinel value for override interruption

# How often to check for the guide's override file (in seconds)
OVERRIDE_CHECK_INTERVAL = 1.0


def setup_logging():
    """
    Sets up logging to write to both a timestamped file and the console (stdout).
    """
    
    # Base configuration: Set log level and output format
    log_format = '%(asctime)s - %(levelname)s - %(funcName)s: %(message)s'
    
    # Remove any existing root handlers to avoid duplicate output
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 1. File Handler
    file_handler = logging.FileHandler(LOG_FILE, mode='w')
    file_handler.setFormatter(logging.Formatter(log_format))
    file_handler.setLevel(logging.DEBUG) # Log everything to the file

    # 2. Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    console_handler.setLevel(logging.INFO) # Only show INFO and above on console

    # Root Logger Configuration
    logging.basicConfig(
        level=logging.DEBUG, # Set the minimum level for the root logger
        handlers=[file_handler, console_handler]
    )
    
    # Log where the file is being saved
    logging.info(f"Logging initialized. Output saved to: {LOG_FILE}")


# --- 2. HELPER FUNCTIONS ---

def is_remote_path(path: str) -> bool:
    """Checks if a path starts with a known remote protocol."""
    return path.lower().startswith(('http://', 'https://', 'ftp://'))


def graceful_exit(signum=None, frame=None):
    """Handles script termination gracefully (e.g., when user presses Ctrl+C)."""
    logging.info("Received signal, performing graceful shutdown...")
    
    # Attempt to stop VLC if it's running
    global VLC_PROCESS
    if VLC_PROCESS and VLC_PROCESS.poll() is None:
        VLC_PROCESS.terminate()
        logging.info("Terminated VLC process.")
        
    sys.exit(0)

def load_channel_list():
    """
    Loads the channel list JSON and returns the list of channel names 
    from the 'channel_order' key.
    """
    try:
        with open(CHANNEL_LIST_FILE, 'r') as f:
            data = json.load(f)
            
            if 'channel_order' in data and isinstance(data['channel_order'], list):
                return data['channel_order']
            else:
                logging.error(f"Channel list file {CHANNEL_LIST_FILE} is missing the 'channel_order' list.")
                return []
                
    except FileNotFoundError:
        logging.error(f"Channel list file not found at {CHANNEL_LIST_FILE}. Cannot determine default channel.")
        return []
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {CHANNEL_LIST_FILE}.")
        return []

def load_schedule_for_channel(channel_name, schedule_date):
    """
    Loads the schedule from a JSON file based on the channel and date.
    Returns the list of scheduled program dictionaries.
    """
    schedule_filename = f"{channel_name}_{schedule_date}_schedule.json"
    schedule_path = os.path.join(SCHEDULE_DIR, schedule_filename)
    
    try:
        with open(schedule_path, 'r') as f:
            schedule_data = json.load(f)
            
            if not isinstance(schedule_data, list) or not schedule_data:
                logging.error(f"Schedule file {schedule_filename} is empty or malformed.")
                return []
                
            logging.info(f"Loaded {len(schedule_data)} program segments for {channel_name} on {schedule_date}.")
            return schedule_data
            
    except FileNotFoundError:
        logging.error(f"Schedule file not found: {schedule_path}.")
        return []
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {schedule_path}.")
        return []
    except Exception as e:
        logging.error(f"Error loading schedule: {e}")
        return []

def load_filler_videos_from_manifest(base_path, manifest_path):
    """
    Loads a list of filler video data (path and duration) from the specified XML manifest,
    resolving the manifest path relative to the base_path.
    """
    abs_manifest_path = os.path.join(base_path, manifest_path)
    
    if not os.path.exists(abs_manifest_path):
        logging.error(f"Manifest file not found: {abs_manifest_path}")
        return []

    filler_list = []
    
    try:
        tree = ET.parse(abs_manifest_path)
        root = tree.getroot()
        
        for file_elem in root.findall('file'):
            file_path = file_elem.get('name')
            length_elem = file_elem.find('length')
            
            if file_path and length_elem is not None and length_elem.text:
                try:
                    duration = float(length_elem.text)
                    filler_list.append({
                        'path': file_path,
                        'duration': duration
                    })
                except ValueError:
                    logging.warning(f"Skipping filler entry due to invalid duration: {length_elem.text}")
                    continue
                except Exception as e:
                    logging.warning(f"Skipping filler entry due to an error: {e}")
                    continue
        
        return filler_list
        
    except ET.ParseError as e:
        logging.error(f"Error parsing XML filler manifest {manifest_path}: {e}")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred while loading filler manifest: {e}")
        return []


def run_filler_break(filler_xml_path, duration_seconds, content_root):
    """
    Plays filler content for a specified duration, loading video paths and durations 
    from the manifest specified by filler_xml_path.
    
    Returns True if interrupted by an override, False otherwise.
    """
    global CURRENT_SIMULATED_TIME, DRY_RUN
    
    if duration_seconds <= 1.0:
        logging.warning("FILLER: Requested duration is too short (<1s). Skipping filler break.")
        return False
    
    filler_list = load_filler_videos_from_manifest(content_root, filler_xml_path) 
    
    if not filler_list:
        logging.error(f"FILLER: Skipping break. Could not load filler videos from {filler_xml_path}.")
        return False
        
    logging.info(f"FILLER: Starting break for {duration_seconds:.2f}s, using manifest: {filler_xml_path} (Items: {len(filler_list)})")
    
    if DRY_RUN:
        # Dry Run Mode: Simulate duration instantly
        logging.warning(f"DRY RUN: Simulating filler break for {duration_seconds:.2f}s.")
        CURRENT_SIMULATED_TIME += datetime.timedelta(seconds=duration_seconds)
        logging.info(f"FILLER END: Simulated break completed. Simulated time advanced to {CURRENT_SIMULATED_TIME}.")
        return False

    # REAL PLAYBACK MODE
    start_time = time.time()
    
    while (time.time() - start_time) < duration_seconds:
        time_left = duration_seconds - (time.time() - start_time)
        
        # Check for user override every time we loop for a new filler clip
        if check_for_override(time_left):
            return True # Signal that an override occurred
            
        if time_left < 1.0:
            logging.debug("FILLER: Time remaining is less than 1 second. Exiting filler loop.")
            break

        filler_video_data = random.choice(filler_list)
        
        filler_duration = filler_video_data['duration']
        # Max clip run time must also respect time_left AND the video's actual duration
        max_clip_run_time = min(filler_duration, time_left)
        
        # --- Resolve path for filler video before calling play_video ---
        raw_filler_path = filler_video_data['path']
        
        if is_remote_path(raw_filler_path) or os.path.isabs(raw_filler_path):
            # If path is remote or already absolute, use it directly
            full_filler_path = raw_filler_path
        else:
            # Otherwise, assume it's relative to the channel's content root
            full_filler_path = os.path.join(content_root, raw_filler_path)
            
        playback_filler_data = {
            'path': full_filler_path,
            'duration': filler_duration
        }
        # --- END FIX ---

        logging.debug(f"FILLER: Playing {os.path.basename(raw_filler_path)} (Length: {filler_duration:.2f}s) for max {max_clip_run_time:.2f}s.") 
        
        # Play the filler video.
        played_time = play_video(playback_filler_data, os.path.basename(raw_filler_path), max_clip_run_time, is_filler=True)

        if played_time == OVERRIDE_INTERRUPTED:
            return True # Propagate override interruption

        if played_time is None:
            logging.error("FILLER: Filler playback failed. Consuming 5 seconds from the break duration.")
            time.sleep(5) 
            
    actual_filler_duration = time.time() - start_time
    logging.info(f"FILLER END: Break completed. Ran for {actual_filler_duration:.2f} seconds.")
    return False


def play_video(video_data, show_name, max_runtime_seconds, start_offset=0.0, is_filler=False):
    """
    Plays a video file using cvlc with offset and hard cutoff. 
    
    Returns:
    - float: Actual time consumed (max_runtime_seconds for main shows for stability)
    - OVERRIDE_INTERRUPTED (-1.0): If interrupted by user override.
    - None: If playback failed (non-zero exit code).
    """
    global DRY_RUN, CURRENT_SIMULATED_TIME, VLC_PROCESS, VLC_TIMEOUT_BUFFER, OVERRIDE_INTERRUPTED

    path = video_data['path']
    is_remote = is_remote_path(path) 
    
    # 1. Determine Stop Time (Absolute time in the video file) - Used for logging only
    vlc_stop_time = start_offset + max_runtime_seconds 
    
    logging.info(
        f"PLAYING: {show_name} (Remote: {is_remote}). Offset: {start_offset:.2f}s, "
        f"Max Run: {max_runtime_seconds:.2f}s (Stop Time: {vlc_stop_time:.2f}s)."
    )

    if DRY_RUN:
        logging.warning(f"DRY RUN MODE: Skipping actual playback via CVLC.")
        
        CURRENT_SIMULATED_TIME += datetime.timedelta(seconds=max_runtime_seconds)
        logging.info(f"DRY RUN COMPLETE. Simulated time advanced by {max_runtime_seconds:.2f}s to {CURRENT_SIMULATED_TIME}.")
        
        return max_runtime_seconds 

    # --- REAL PLAYBACK LOGIC BELOW ---

    # 2. Build the Base CVLC Command and Scheduling Flags
    command = [VLC_PATH] 
    command.extend(VLC_ARGS)
    
    if is_remote:
        command.extend(REMOTE_STREAMING_FLAGS)
    
    # We only include --start-time. We rely on the Python process timeout (below) for the stop time.
    command.extend([
        f'--start-time={start_offset:.2f}', 
    ])
    
    # 3. Handle Path
    if is_remote:
        try:
            parsed_url = urllib.parse.urlparse(path)
            # URL encoding the path component to handle spaces and special characters
            encoded_path = parsed_url._replace(path=urllib.parse.quote(parsed_url.path)).geturl()
        except Exception:
            encoded_path = path
        command.append(encoded_path)
    else:
        command.append(path)

    # 4. Execute Playback (Non-blocking Popen, then block with wait)
    playback_start_time = time.time()
    
    # Calculate external timeout for the VLC process
    # This is the scheduled run time + buffer, ensuring we maintain schedule stability.
    external_timeout = max_runtime_seconds + VLC_TIMEOUT_BUFFER 
    
    # The actual sleep/wait duration we'll perform in the loop
    sleep_duration = min(OVERRIDE_CHECK_INTERVAL, max_runtime_seconds)

    try:
        logging.info(f"Executing command: {' '.join(command)}")

        # Start VLC process non-blockingly, but track it globally for graceful exit (Ctrl+C)
        VLC_PROCESS = subprocess.Popen(
            command, 
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Poll the VLC process periodically while checking for an override
        time_elapsed = 0.0
        while VLC_PROCESS.poll() is None and time_elapsed < external_timeout:
            
            # Check for guide override every 'sleep_duration' seconds
            if check_for_override(remaining_time=external_timeout - time_elapsed):
                # check_for_override has handled the termination and video playback
                return OVERRIDE_INTERRUPTED # Signal that the video was interrupted by user

            # Sleep for a short interval or until timeout, whichever is shorter
            time_to_sleep = min(sleep_duration, external_timeout - time_elapsed)
            if time_to_sleep > 0:
                time.sleep(time_to_sleep)
                time_elapsed += time_to_sleep
            else:
                break


        # Process termination outcome
        if VLC_PROCESS.poll() is None:
            # We hit the external timeout
            logging.warning(f"VLC process timed out after {external_timeout:.2f}s. Forcefully terminating process to maintain schedule.")
            VLC_PROCESS.terminate()
            VLC_PROCESS.wait() 
            VLC_PROCESS = None
            # On timeout, we assume the scheduled time was fully consumed.
            return max_runtime_seconds 
        
        # If we reach here, the process exited cleanly (VLC_PROCESS.poll() is not None)
        return_code = VLC_PROCESS.returncode
        VLC_PROCESS = None # Clear the global reference
        
        actual_run_time = time.time() - playback_start_time
        
        if return_code != 0:
            # Playback failed due to non-zero exit code (e.g., file not found, crash - like -11)
            logging.error(f"VLC playback exited with non-zero code: {return_code}. Playback failed! (File/Path likely incorrect or playback unstable: {path})")
            return None

        # Success path (only hit if return_code is 0 and it finished before the external timeout)
        if is_filler:
            logging.info(f"Playback finished successfully. Actual run time: {actual_run_time:.2f}s (FILLER).")
            return actual_run_time
        else:
            # For main shows, if it finishes early, we still assume the full time was consumed
            # to keep the clock synchronized and run filler if needed.
            logging.info(f"Playback finished successfully but early. Assuming consumed time: {max_runtime_seconds:.2f}s (MAIN SHOW STABILITY).")
            return max_runtime_seconds
        
    except FileNotFoundError:
        VLC_PROCESS = None
        logging.error(f"CVLC not found at '{VLC_PATH}'. Cannot play video.")
        return None
    
    except Exception as e:
        VLC_PROCESS = None
        logging.error(f"An unexpected error occurred during playback of {path}: {e}")
        return None

def check_for_override(remaining_time: float) -> bool:
    """
    Checks for the existence of the override file. If found, it kills the current 
    VLC process, plays the selected video, cleans up, and returns True.
    """
    global VLC_PROCESS
    
    if os.path.exists(OVERRIDE_FILE):
        
        # 1. Read the path
        try:
            with open(OVERRIDE_FILE, 'r') as f:
                override_path = f.read().strip()
            os.remove(OVERRIDE_FILE) # Important: Delete the file immediately
            
        except Exception as e:
            logging.error(f"Failed to read/delete override file: {e}")
            return False # Continue normal playback
            
        logging.warning(f"GUIDE OVERRIDE DETECTED. User requested: {override_path}")

        # 2. Terminate the currently running VLC process
        if VLC_PROCESS and VLC_PROCESS.poll() is None:
            logging.warning("Terminating currently playing video to honor user request.")
            VLC_PROCESS.terminate()
            VLC_PROCESS.wait()
            VLC_PROCESS = None
        
        # 3. Play the override video (we assume full duration, let it play until finished)
        # We use a very long timeout here to allow the user to watch the whole video.
        
        override_data = {'path': override_path, 'duration': 3600.0} # Duration is a placeholder, VLC will play till end.
        
        logging.info(f"Playing user selected video: {os.path.basename(override_path)}")
        
        # We call play_video with a large max_runtime to let it play to completion, or until killed.
        # We set start_offset=0.0 and max_runtime_seconds=7200 (2 hours)
        play_video(
            override_data, 
            os.path.basename(override_path), 
            max_runtime_seconds=7200, 
            start_offset=0.0,
            is_filler=False
        )

        # 4. Resume the main scheduler loop by forcing an immediate recalculation
        logging.warning("User video finished/terminated. Scheduler will now jump to the currently active slot.")
        return True # Signal that the calling function should break its loop and recalculate time
    
    return False

def run_channel_day(channel_name, schedule_date, initial_start_time: datetime.datetime):
    """
    The scheduler loop that runs the channel for the specified day until the schedule ends.
    """
    global CURRENT_SIMULATED_TIME, DRY_RUN, OVERRIDE_INTERRUPTED
    
    schedule = load_schedule_for_channel(channel_name, schedule_date)
    if not schedule:
        logging.error(f"Cannot run channel {channel_name}: No schedule loaded.")
        return 
    
    # Find the starting program index based on the initial start time
    start_index = 0
    now = initial_start_time
    
    while True: # Keep looping until we find a valid slot or reach the end
        
        found_start_slot = False
        for i in range(start_index, len(schedule)):
            program = schedule[i]
            try:
                prog_start_datetime = datetime.datetime.strptime(program['start_time'], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=None)
                slot_duration = program['slot_duration_total']
                prog_end_datetime = prog_start_datetime + datetime.timedelta(seconds=slot_duration)
                
                if prog_end_datetime > now:
                    start_index = i
                    found_start_slot = True
                    break
            except (ValueError, KeyError) as e:
                logging.error(f"Skipping malformed schedule entry at index {i}: {e}")
                continue
        
        if not found_start_slot:
            logging.info("End of schedule reached or no more valid slots.")
            break

        current_program_index = start_index 
        logging.info(f"Scheduler starting at index {current_program_index} ({schedule[current_program_index].get('show_name', 'Unknown')})")
        
        if DRY_RUN:
            CURRENT_SIMULATED_TIME = now
            logging.info(f"DRY RUN: Initial simulated time set to: {CURRENT_SIMULATED_TIME}")
        
        CHANNEL_CONTENT_ROOT = schedule[current_program_index].get('content_root', BASE_CONTENT_PATH)
        logging.info(f"Channel Content Root set to: {CHANNEL_CONTENT_ROOT}")

        # --- MAIN PLAYBACK LOOP ---
        while current_program_index < len(schedule):

            # Check for user override at the start of the slot
            if check_for_override(remaining_time=300): # Use a 5-minute dummy value
                # If an override occurred, break the inner loop and restart the outer loop to recalculate 'now'
                break 

            program = schedule[current_program_index]
            
            current_time = CURRENT_SIMULATED_TIME if DRY_RUN else datetime.datetime.now().replace(tzinfo=None)
            
            prog_start_datetime = datetime.datetime.strptime(program['start_time'], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=None)
            slot_duration = program['slot_duration_total']
            prog_end_datetime = prog_start_datetime + datetime.timedelta(seconds=slot_duration)
            
            
            # Log the start of the slot processing
            logging.info(f"\n--- START SLOT: {program.get('show_name', 'Program')} ---")
            if DRY_RUN:
                logging.info(f"DRY RUN TIME: {current_time}")
            logging.info(f"Slot Start: {prog_start_datetime}, Slot End: {prog_end_datetime}, Current Time: {current_time}")
            
            # --- A. Pre-Program Wait (If we are early) ---
            time_to_start = (prog_start_datetime - current_time).total_seconds()
            
            if time_to_start > 1.0: 
                logging.warning(f"WAIT: Current time is ahead of schedule. Sleeping/Simulating wait for {time_to_start:.2f} seconds until {prog_start_datetime}.")
                
                if DRY_RUN:
                    CURRENT_SIMULATED_TIME += datetime.timedelta(seconds=time_to_start)
                    current_time = CURRENT_SIMULATED_TIME
                else:
                    # While waiting, we can check for overrides
                    wait_start = time.time()
                    override_occurred = False
                    while (time.time() - wait_start) < time_to_start:
                        if check_for_override(time_to_start - (time.time() - wait_start)):
                            override_occurred = True
                            break # Break the wait loop and restart the whole schedule loop
                        time.sleep(OVERRIDE_CHECK_INTERVAL)
                    
                    if override_occurred or check_for_override(0): # Check one last time after the wait
                        break
                        
                    current_time = datetime.datetime.now().replace(tzinfo=None) 
                    
                logging.info(f"Wait finished. Actual slot start time reached.")
            elif time_to_start < -1.0:
                logging.warning(f"LATE: Scheduler is running {abs(time_to_start):.2f} seconds late for this slot.")

            
            # --- B. EXECUTE PLAYBACK LOGIC ---
            
            current_time = CURRENT_SIMULATED_TIME if DRY_RUN else datetime.datetime.now().replace(tzinfo=None)
            
            time_elapsed_in_slot = (current_time - prog_start_datetime).total_seconds()
            time_available_seconds = slot_duration - time_elapsed_in_slot
            
            if time_available_seconds <= 1.0:
                # Slot has already passed or is too short to bother.
                current_program_index += 1
                continue

            video_data = program['video_data']
            video_full_duration = video_data.get('duration', 0)
            
            # --- USER REQUEST MODIFICATION START ---
            # Set offset to 0.0 to ensure the program always starts from the beginning,
            # regardless of how late the scheduler is.
            start_offset = 0.0 
            
            # The maximum time we allow the player to run is the minimum of:
            # 1. The full video duration.
            # 2. The time remaining in the scheduled slot (time_available_seconds).
            max_run_time = min(video_full_duration, time_available_seconds)
            # --- USER REQUEST MODIFICATION END ---
            
            filler_manifest_path = program.get('filler_xml_path', 'ads/default_ads.xml')
            
            raw_video_path = program['video_data']['path']
            
            # --- Determine the full path to the video. Only join with CHANNEL_CONTENT_ROOT if it's not a URL. ---
            if is_remote_path(raw_video_path):
                video_path_to_play = raw_video_path
            else:
                # Assume local path (absolute or relative to content root)
                video_path_to_play = os.path.join(CHANNEL_CONTENT_ROOT, raw_video_path)
            # --- END FIX ---
                
            playback_video_data = {
                'path': video_path_to_play,
                'duration': video_full_duration
            }

            # --- C. Play Main Show or Filler ---
            
            if max_run_time < MIN_PLAYBACK_TIME: 
                # run_filler_break now returns True if overridden, or False otherwise
                logging.info(f"Slot too short ({max_run_time:.2f}s) for main show. Running filler for remaining time.")
                if run_filler_break(filler_manifest_path, time_available_seconds, CHANNEL_CONTENT_ROOT):
                    break # Restart outer loop on override
                
            else:
                # 1. Play the main program.
                actual_run_time = play_video(
                    playback_video_data, 
                    program['show_name'], 
                    max_run_time, 
                    start_offset=start_offset, # Now always 0.0
                    is_filler=False 
                )
                
                # If play_video was interrupted by an override, restart the whole schedule loop.
                if actual_run_time == OVERRIDE_INTERRUPTED:
                    logging.warning("Playback interrupted by user override. Recalculating schedule time.")
                    break # Restart outer loop (which recalculates 'now')
                
                # --- D. Handle Failures or Fill the Gap (Ads/Filler) ---
                
                # The time consumed is equal to max_run_time (the full time allotted for the show in the schedule)
                # unless VLC fails (actual_run_time is None)
                if actual_run_time is not None:
                    time_remaining_in_slot = time_available_seconds - actual_run_time 

                    if time_remaining_in_slot > 1.0:
                        logging.info(f"Show finished/cut short. Running filler for remaining time: {time_remaining_in_slot:.2f}s.")
                        if run_filler_break(filler_manifest_path, time_remaining_in_slot, CHANNEL_CONTENT_ROOT):
                            break # Restart outer loop on override from filler break
                            
                    elif time_remaining_in_slot < -1.0:
                        # Should not happen if play_video correctly returns max_run_time on timeout
                        logging.warning(f"SLOT OVERRUN: Program ran {abs(time_remaining_in_slot):.2f}s past the scheduled slot end time!")
                    else:
                        logging.info(f"Slot ended precisely. Remaining time: {time_remaining_in_slot:.2f}s.")
                        
                elif actual_run_time is None:
                    # Non-override failure (e.g., VLC crash - like the -11 error)
                    logging.error("MAIN SHOW FAILED. Running filler for the full time available.")
                    if run_filler_break(filler_manifest_path, time_available_seconds, CHANNEL_CONTENT_ROOT):
                        break # Restart outer loop on override from filler break

            
            # --- E. Advance ---
            
            logging.info(f"--- END SLOT: {program.get('show_name', 'Program')}. Advancing to next program. ---")
            current_program_index += 1
            
        
        # If the inner loop broke due to an override, we restart the outer loop.
        # Otherwise, the inner loop finished the day's schedule.
        now = datetime.datetime.now().replace(tzinfo=None) 
        
    logging.info("--- End of Schedule Reached. Exiting. ---")


def main_loop(args):
    """
    Main execution logic for a single channel run.
    """
    global DRY_RUN
    
    DRY_RUN = args.dry_run

    channel_order = load_channel_list()
    if not channel_order:
        logging.error("No channels defined in channel_list.json. Exiting.")
        sys.exit(1)

    # Determine channel to run, defaulting to the first one
    channel_to_run = args.channel
    if not channel_to_run or channel_to_run not in channel_order:
        channel_to_run = channel_order[0]
        logging.info(f"No specific channel provided or found. Defaulting to '{channel_to_run}'.")

    run_date_str = datetime.date.today().strftime("%Y-%m-%d")

    # Determine start time based on args
    if DRY_RUN and args.simulate_time:
        try:
            start_time_str = f"{run_date_str}T{args.simulate_time}"
            initial_start_datetime = datetime.datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=None)
        except ValueError:
            logging.error("Invalid time format for --simulate-time. Using current time.")
            initial_start_datetime = datetime.datetime.now().replace(tzinfo=None)
    else:
        initial_start_datetime = datetime.datetime.now().replace(tzinfo=None)

    
    logging.info(f"--- Starting Channel Scheduler for '{channel_to_run}' on {run_date_str} (Mode: {'DRY-RUN' if DRY_RUN else 'LIVE'}) ---")
    
    # Run the channel schedule
    run_channel_day(channel_to_run, run_date_str, initial_start_datetime)
        
    logging.info("Scheduler process finished.")


if __name__ == '__main__':
    setup_logging()
    
    parser = argparse.ArgumentParser(description="A robust TV channel scheduler using VLC.")
    parser.add_argument('channel', nargs='?', type=str, 
                        help="The name of the channel to run (overrides default).")
    parser.add_argument('--dry-run', action='store_true', 
                        help="Simulate the scheduling and logging without playing videos.")
    parser.add_argument('--simulate-time', type=str, default=None,
                        help="Start dry-run simulation at a specific time (HH:MM:SS) on the run date.")

    args = parser.parse_args()
    
    try:
        # Register the graceful exit handler for Ctrl+C
        signal.signal(signal.SIGINT, graceful_exit)
        signal.signal(signal.SIGTERM, graceful_exit)
        
        main_loop(args)
    except KeyboardInterrupt:
        graceful_exit()
    except Exception as e:
        logging.critical(f"A fatal unhandled error occurred: {e}")
        sys.exit(1)
