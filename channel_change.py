import sys
import json
import os
import argparse
import logging

# --- Configuration (Must match tvplayer.py) ---
# Ensure these paths are correct relative to where you run tvplayer.py
BASE_PATH = os.path.dirname(os.path.abspath(__file__))
CHANNEL_LIST_FILE = os.path.join(BASE_PATH, 'channel_configs', 'channel_list.json')
CHANNEL_STATE_FILE = os.path.join(BASE_PATH, 'current_channel.txt')
CHANNEL_REQUEST_FILE = os.path.join(BASE_PATH, 'channel_request.txt')

# Setup basic logging to console for user feedback
# The print() statement is also used for immediate confirmation in the terminal.
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def load_channel_order() -> list[str]:
    """Loads the ordered list of channel names from channel_list.json."""
    try:
        with open(CHANNEL_LIST_FILE, 'r') as f:
            data = json.load(f)
            return data.get('channel_order', [])
    except FileNotFoundError:
        logging.error(f"Channel list file not found at {CHANNEL_LIST_FILE}")
        return []
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {CHANNEL_LIST_FILE}")
        return []

def get_current_channel() -> Optional[str]:
    """Reads the name of the currently active channel from the state file."""
    try:
        with open(CHANNEL_STATE_FILE, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except Exception:
        return None

def main():
    parser = argparse.ArgumentParser(description="Requests a channel switch (up or down) for the TV Player.")
    parser.add_argument('direction', type=str, choices=['up', 'down'], help="Direction to switch the channel.")
    args = parser.parse_args()

    channel_order = load_channel_order()
    if not channel_order:
        logging.error("No channels found in channel list. Cannot switch.")
        sys.exit(1)

    current_channel = get_current_channel()
    
    if current_channel not in channel_order:
        # If state file is missing or invalid, we can't reliably calculate the next channel.
        # It's safest to exit with a warning, or force it to the first channel (index 0).
        current_index = 0
        logging.warning(f"Could not determine current channel state. Defaulting index to 0.")
    else:
        current_index = channel_order.index(current_channel)

    # Calculate new index using modulo for wrap-around (e.g., last channel UP goes to first)
    num_channels = len(channel_order)
    if args.direction == 'up':
        # Up means moving to the NEXT channel (index + 1)
        new_index = (current_index + 1) % num_channels
    elif args.direction == 'down':
        # Down means moving to the PREVIOUS channel (index - 1)
        new_index = (current_index - 1 + num_channels) % num_channels
    else:
        # Should be caught by argparse
        sys.exit(1)

    new_channel = channel_order[new_index]

    if new_channel == current_channel:
        # This happens if there's only one channel, or if the state was corrupted and fixed to index 0.
        print(f"⚠️ Already on '{current_channel}'. No change needed.")
        sys.exit(0)

    # Write the new channel name to the request file
    try:
        with open(CHANNEL_REQUEST_FILE, 'w') as f:
            f.write(new_channel)
        
        print(f"✅ Request submitted: Switching to channel '{new_channel}'")
        
    except Exception as e:
        logging.critical(f"Failed to write channel request file: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
