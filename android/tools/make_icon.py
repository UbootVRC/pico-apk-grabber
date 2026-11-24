"""Draw the launcher icon (an original design: download arrow into a tray) at two sizes."""
import os
import sys

from PIL import Image, ImageDraw

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "res")


def draw(size):
    s = 4  # supersample
    n = size * s
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([n * 0.04, n * 0.04, n * 0.96, n * 0.96], radius=n * 0.22, fill=(32, 38, 58, 255))
    d.rounded_rectangle([n * 0.04, n * 0.04, n * 0.96, n * 0.96], radius=n * 0.22,
                        outline=(92, 196, 106, 255), width=int(n * 0.03))
    g = (92, 196, 106, 255)
    w = n * 0.12
    d.rectangle([n / 2 - w / 2, n * 0.20, n / 2 + w / 2, n * 0.52], fill=g)
    d.polygon([(n * 0.28, n * 0.48), (n * 0.72, n * 0.48), (n / 2, n * 0.70)], fill=g)
    t = n * 0.07
    d.rectangle([n * 0.22, n * 0.76, n * 0.78, n * 0.76 + t], fill=(230, 235, 245, 255))
    d.rectangle([n * 0.22, n * 0.62, n * 0.22 + t, n * 0.76 + t], fill=(230, 235, 245, 255))
    d.rectangle([n * 0.78 - t, n * 0.62, n * 0.78, n * 0.76 + t], fill=(230, 235, 245, 255))
    return img.resize((size, size), Image.LANCZOS)


for folder, size in (("mipmap-mdpi", 48), ("mipmap-xxxhdpi", 192)):
    os.makedirs(os.path.join(out, folder), exist_ok=True)
    draw(size).save(os.path.join(out, folder, "ic_launcher.png"))
print("icons written")
