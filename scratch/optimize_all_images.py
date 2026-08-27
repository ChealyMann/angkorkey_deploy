import os
from PIL import Image

img_dir = '/var/www/angkorkey/static/images/'
total_saved = 0
count = 0

for root, _, files in os.walk(img_dir):
    for f in files:
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            path = os.path.join(root, f)
            try:
                size_before = os.path.getsize(path)
                with Image.open(path) as img:
                    w, h = img.size
                    changed = False
                    
                    # Category icons or banners: Max 800px is more than enough for crisp 2x retina display
                    if w > 800 or h > 800:
                        img.thumbnail((800, 800), Image.Resampling.LANCZOS)
                        changed = True
                    
                    # Compress and optimize if size > 25KB or changed
                    if size_before > 25 * 1024 or changed:
                        if f.lower().endswith(('.jpg', '.jpeg')):
                            if img.mode in ('RGBA', 'P'):
                                img = img.convert('RGB')
                            img.save(path, 'JPEG', quality=80, optimize=True)
                        elif f.lower().endswith('.png'):
                            if img.mode == 'RGBA':
                                alpha = img.getchannel('A')
                                if alpha.getextrema() == (255, 255):
                                    img = img.convert('RGB')
                                    img.save(path, 'JPEG', quality=80, optimize=True)
                                else:
                                    img.save(path, 'PNG', optimize=True)
                            else:
                                if img.mode != 'RGB':
                                    img = img.convert('RGB')
                                img.save(path, 'JPEG', quality=80, optimize=True)
                        elif f.lower().endswith('.webp'):
                            img.save(path, 'WEBP', quality=80, method=6)

                size_after = os.path.getsize(path)
                saved = size_before - size_after
                if saved > 0:
                    total_saved += saved
                    count += 1
                    print(f"[{count}] Optimized {f}: {size_before//1024}KB -> {size_after//1024}KB (-{saved//1024}KB)")
            except Exception as e:
                print(f"Error processing {f}: {e}")

print(f"\n==========================================")
print(f" Optimization Finished: {count} images processed")
print(f" Total bandwidth saved: {total_saved // 1024 // 1024} MB ({total_saved // 1024} KB)")
print(f"==========================================")
