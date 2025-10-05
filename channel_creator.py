import os
import xml.etree.ElementTree as ET
import argparse
import json
import shutil # New import for copying template files (optional, but good practice)

# --- Configuration Constants (ADJUST THESE PATHS TO YOUR SETUP) ---
# Directory where your source XML template lives (and where channel_list.json lives)
SCHEDULE_CONFIG_DIR = '/home/markd/raspberry_pi_TV/channel_configs/' 
# Base directory for ALL channel content (e.g., /home/markd/rasppery_pi_TV/bbc, /home/markd/rasppery_pi_TV/itv)
CONTENT_BASE_DIR = '/home/markd/raspberry_pi_TV/' 
# The template file to copy from
TEMPLATE_CHANNEL_NAME = 'bbc' 


def create_new_channel_template(new_channel_name: str):
    """
    Creates a new channel configuration and folder structure based on the BBC template.
    """
    
    new_channel_name = new_channel_name.lower()
    
    # 1. Define Paths
    template_xml_filename = f"{TEMPLATE_CHANNEL_NAME}_channel.xml"
    new_xml_filename = f"{new_channel_name}_channel.xml"
    
    template_xml_path = os.path.join(SCHEDULE_CONFIG_DIR, template_xml_filename)
    new_xml_path = os.path.join(SCHEDULE_CONFIG_DIR, new_xml_filename)
    
    # The new channel's root content directory (e.g., /home/markd/rasppery_pi_TV/itv)
    new_content_root = os.path.join(CONTENT_BASE_DIR, new_channel_name)
    
    print(f"--- Setting up new channel: {new_channel_name.upper()} ---")

    if os.path.exists(new_xml_path):
        print(f"❌ Error: Configuration file already exists at {new_xml_path}")
        return

    if not os.path.exists(template_xml_path):
        print(f"❌ Error: Template file not found at {template_xml_path}")
        print(f"   Ensure {template_xml_filename} exists in {SCHEDULE_CONFIG_DIR}")
        return

    # 2. Read, Parse, and Modify the XML
    try:
        tree = ET.parse(template_xml_path)
        root = tree.getroot()
        
        # Update Root Tag Attributes (name, content_root, background_image_source)
        root.set('name', new_channel_name)
        root.set('content_root', new_content_root)
        
        # Set the background source path (e.g., /home/markd/.../itv/idents)
        new_background_source = os.path.join(new_content_root, 'idents')
        root.set('background_image_source', new_background_source)
        
        # 3. Write the New XML File
        # Use pretty_print=True for better readability (if available)
        try:
             # Standard method for writing XML
             tree.write(new_xml_path, encoding='UTF-8', xml_declaration=True)
        except Exception:
             # Fallback if specific formatting fails
             tree.write(new_xml_path, encoding='UTF-8', xml_declaration=True)

        print(f"✅ Configuration file created at: {new_xml_path}")
        
    except Exception as e:
        print(f"❌ Error processing XML: {e}")
        return

    # 4. Create Channel Content Folders
    try:
        # Create the main content root folder
        os.makedirs(new_content_root, exist_ok=True)

        # Get list of required subfolders from the template's slots and standard assets
        required_subfolders = set(['ads', 'idents']) # Always needed
        for slot in root.findall('slot'):
            folder = slot.get('folder')
            if folder:
                required_subfolders.add(folder)
        
        for folder in required_subfolders:
            folder_path = os.path.join(new_content_root, folder)
            os.makedirs(folder_path, exist_ok=True)
            
        print(f"✅ Content structure created at: {new_content_root} with subfolders: {', '.join(required_subfolders)}")

    except Exception as e:
        print(f"❌ Error creating folders: {e}")

    # 5. Update Channel List (channel_list.json)
    channel_list_path = os.path.join(SCHEDULE_CONFIG_DIR, 'channel_list.json')
    try:
        if os.path.exists(channel_list_path):
            with open(channel_list_path, 'r+') as f:
                data = json.load(f)
                channel_order = data.get('channel_order', [])
                
                if new_channel_name not in channel_order:
                    channel_order.append(new_channel_name)
                    f.seek(0)
                    json.dump({"channel_order": channel_order}, f, indent=4)
                    f.truncate()
                    print(f"✅ Channel '{new_channel_name}' added to channel_list.json for surfing.")
                else:
                    print(f"ℹ️ Channel '{new_channel_name}' already in channel_list.json.")
        else:
            print(f"⚠️ Warning: {channel_list_path} not found. Skipping update.")
            
    except Exception as e:
        print(f"❌ Error updating channel_list.json: {e}")


if __name__ == '__main__':
    # Setup argument parser for command line
    parser = argparse.ArgumentParser(
        description="Create a new channel configuration and folder structure based on the 'bbc' template."
    )
    parser.add_argument(
        'channel_name', 
        type=str, 
        help='The name of the new channel (e.g., ITV, Channel4). This name will be converted to lowercase.'
    )
    
    args = parser.parse_args()
    
    create_new_channel_template(args.channel_name)