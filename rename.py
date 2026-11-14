import os

def rename_images(directory):
    # Ensure the directory exists
    if not os.path.exists(directory):
        print(f"Error: Directory '{directory}' does not exist.")
        return

    # List all files and filter for .jpg
    files = [f for f in os.listdir(directory) if f.lower().endswith('.jpg')]
    
    # Sort files to ensure consistent renaming order
    files.sort()

    print(f"Found {len(files)} images. Renaming...")

    for index, filename in enumerate(files, start=1):
        # Construct the new filename
        new_name = f"image{index}.jpg"
        
        # Get full paths
        old_path = os.path.join(directory, filename)
        new_path = os.path.join(directory, new_name)
        
        # Rename
        os.rename(old_path, new_path)
        print(f"Renamed: {filename} -> {new_name}")

    print("Renaming complete.")

if __name__ == "__main__":
    target_dir = "/home/admin/coffee_bean/capture/images"
    rename_images(target_dir)