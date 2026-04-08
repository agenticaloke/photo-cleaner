"""Generate small test fixture images for unit tests."""
import os
from PIL import Image, ImageDraw

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def create_fixtures():
    os.makedirs(FIXTURES_DIR, exist_ok=True)

    # Original: a 220x220 image with a distinct pattern
    img = Image.new("RGB", (220, 220), color=(30, 120, 200))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 180, 180], fill=(255, 200, 50))
    draw.ellipse([70, 70, 150, 150], fill=(200, 30, 30))
    img.save(os.path.join(FIXTURES_DIR, "thumb_original.jpg"), "JPEG", quality=95)

    # Duplicate: byte-identical copy
    img.save(os.path.join(FIXTURES_DIR, "thumb_duplicate.jpg"), "JPEG", quality=95)

    # Similar: resized down then back up (perceptually similar, different bytes)
    small = img.resize((110, 110), Image.LANCZOS)
    resized_back = small.resize((220, 220), Image.LANCZOS)
    resized_back.save(os.path.join(FIXTURES_DIR, "thumb_similar.jpg"), "JPEG", quality=85)

    # Different: a completely different image
    diff = Image.new("RGB", (220, 220), color=(10, 50, 10))
    draw2 = ImageDraw.Draw(diff)
    draw2.line([(0, 0), (220, 220)], fill=(255, 255, 255), width=5)
    draw2.line([(220, 0), (0, 220)], fill=(255, 255, 255), width=5)
    diff.save(os.path.join(FIXTURES_DIR, "thumb_different.jpg"), "JPEG", quality=95)

    print(f"Created 4 fixture images in {FIXTURES_DIR}")


if __name__ == "__main__":
    create_fixtures()
