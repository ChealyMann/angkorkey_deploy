# import os
# import uuid

# from PIL import Image, ImageEnhance
# from werkzeug.utils import secure_filename


# def allowed_file(filename, allowed_extensions):
#     return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


# def save_image(
#     file, upload_folder, allowed_extensions, resize_to=(800, 800), thumb_size=(100, 100)
# ):
#     if not file or file.filename == "":
#         return "no file"

#     if not allowed_file(file.filename, allowed_extensions):
#         return "invalid file"

#     filename = secure_filename(file.filename)
#     random_hex = uuid.uuid4().hex

#     # changing the name to random hex
#     name, ext = os.path.splitext(filename)
#     filename = name + random_hex + ext

#     # split the name and ext from filename
#     usage_name, ext = os.path.splitext(filename)

#     original_path = os.path.join(upload_folder, filename)
#     resized_path = os.path.join(upload_folder, f"resized_{usage_name}{ext}")
#     thumb_path = os.path.join(upload_folder, f"thumb_{usage_name}{ext}")

#     file.save(original_path)

#     # base and log
#     base = Image.open(original_path)
#     logo = Image.open("static/images/logo.png").convert("RGBA")

#     opacity = 0.0

#     alpha = logo.split()[3]
#     alpha = ImageEnhance.Brightness(alpha).enhance(opacity)
#     logo.putalpha(alpha)

#     new_width = int(base.width * 0.2)
#     new_height = int(logo.height * (new_width / logo.width))

#     logo = logo.resize((new_width, new_height), Image.LANCZOS)
#     logo = logo.rotate(45, expand=True)

#     for x in range(0, base.width, logo.width + 10):
#         for y in range(0, base.height, logo.height + 10):
#             base.paste(logo, (x, y), logo)

#     base.save(original_path)

#     image = Image.open(original_path)

#     resized = image.copy()
#     resized.thumbnail(resize_to)
#     resized.save(resized_path)

#     thumb = image.copy()
#     thumb.thumbnail(thumb_size)
#     thumb.save(thumb_path)

#     return filename

import os
import uuid

from PIL import Image
from flask import current_app
from werkzeug.utils import secure_filename

def allowed_file(filename, allowed_extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions

def save_image(file, upload_folder, allowed_extensions, resize_to=(800, 800), thumb_size=(100, 100)):
    # Default fallback if nothing is uploaded
    if not file or file.filename == "":
        return "none.jpg"

    if not allowed_file(file.filename, allowed_extensions):
        return "none.jpg"

    # Generate random hex filename
    filename = secure_filename(file.filename)
    random_hex = uuid.uuid4().hex
    name, ext = os.path.splitext(filename)
    filename = name + random_hex + ext
    usage_name, ext = os.path.splitext(filename)

    # Build absolute paths using Flask's root path so cPanel never gets lost
    original_path = os.path.join(current_app.root_path, upload_folder, filename)
    resized_path = os.path.join(current_app.root_path, upload_folder, f"resized_{usage_name}{ext}")
    thumb_path = os.path.join(current_app.root_path, upload_folder, f"thumb_{usage_name}{ext}")

    # Ensure the target directory exists
    os.makedirs(os.path.dirname(original_path), exist_ok=True)

    try:
        # Open file directly in memory (Bypasses cPanel permission crashes!)
        img = Image.open(file)

        # Convert RGBA/Palette to RGB if saving as JPEG/WebP to prevent crashes
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Save Normal Image (compressed)
        save_kwargs = {'optimize': True}
        if ext.lower() in ('.jpg', '.jpeg'):
            save_kwargs['quality'] = 82
        elif ext.lower() == '.webp':
            save_kwargs['quality'] = 82
            save_kwargs['method'] = 6

        img.save(original_path, **save_kwargs)

        # Create and save Resized Image
        resized = img.copy()
        resized.thumbnail(resize_to, Image.Resampling.LANCZOS)
        resized.save(resized_path, **save_kwargs)

        # Create and save Thumbnail Image
        thumb = img.copy()
        thumb.thumbnail(thumb_size, Image.Resampling.LANCZOS)
        thumb.save(thumb_path, **save_kwargs)

    except Exception as e:
        print(f"CRITICAL UPLOAD ERROR: {e}")
        return "none.jpg"

    return filename