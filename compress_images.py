
import os
import zipfile
from pathlib import Path

# Directory containing images
IMAGE_DIR = Path("/home/admin/coffee_bean/capture/images/mixed")

# Number of images per ZIP file
BATCH_SIZE = 100

# Supported image extensions
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp",
    ".gif", ".webp", ".tiff", ".tif"
}

def compress_images():
    # Get all image files, sorted by filename
    images = sorted([
        f for f in IMAGE_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ])

    total_images = len(images)

    if total_images == 0:
        print("No images found!")
        return

    print(f"Found {total_images} images.")

    # Split images into batches of 100
    for batch_num, start in enumerate(
        range(0, total_images, BATCH_SIZE), start=1
    ):
        batch = images[start:start + BATCH_SIZE]

        zip_name = IMAGE_DIR / f"image_batch_{batch_num:03d}.zip"

        with zipfile.ZipFile(
            zip_name,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6
        ) as zipf:

            for image in batch:
                zipf.write(image, arcname=image.name)

        print(
            f"Created {zip_name.name}: "
            f"{len(batch)} images"
        )

    print("\nCompression completed!")
    print(f"Total images: {total_images}")
    print(f"Total ZIP files: {(total_images + BATCH_SIZE - 1) // BATCH_SIZE}")


if __name__ == "__main__":
    compress_images()
