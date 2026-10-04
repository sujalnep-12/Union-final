from pathlib import Path
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = Path("headset_sasto_promo.mp4")
W, H, FPS, DURATION = 720, 1280, 30, 13
FONT = r"C:\Windows\Fonts\NirmalaB.ttf"
WHITE = (250, 252, 255)
BLUE = (37, 138, 255)


def cover(image, width, height):
    scale = max(width / image.width, height / image.height)
    im = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    return im.crop(((im.width - width) // 2, (im.height - height) // 2,
                    (im.width + width) // 2, (im.height + height) // 2))


def font(size):
    return ImageFont.truetype(FONT, size)


def centered(draw, text, y, size, fill=WHITE, spacing=8):
    f = font(size)
    box = draw.multiline_textbbox((0, 0), text, font=f, spacing=spacing, align="center")
    draw.multiline_text(((W - (box[2] - box[0])) / 2, y), text, font=f, fill=fill,
                        spacing=spacing, align="center")


person = Image.open(r"C:\Users\Sujal Gaire\Downloads\ChatGPT Image Aug 10, 2026, 04_55_54 PM.png").convert("RGB")
product = Image.open(r"C:\Users\Sujal Gaire\Downloads\images.jpgww.png").convert("RGB")
person_bg = cover(person, W, H).filter(ImageFilter.GaussianBlur(18)).convert("RGBA")
product_bg = cover(product, W, H).filter(ImageFilter.GaussianBlur(18)).convert("RGBA")

cmd = [r"C:\Program Files (x86)\Wondershare\Recoverit\ffmpeg.exe", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
       "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
       "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT)]
process = subprocess.Popen(cmd, stdin=subprocess.PIPE)

for i in range(FPS * DURATION):
    t = i / FPS
    if t < 4:
        base = person_bg.copy()
        panel = cover(person, 620, 850).convert("RGBA")
        zoom = 1 + 0.025 * (t / 4)
        panel = panel.resize((round(620 * zoom), round(850 * zoom)), Image.Resampling.LANCZOS)
        base.alpha_composite(panel, ((W - panel.width) // 2, 270))
        shade = Image.new("RGBA", (W, H), (0, 8, 26, 105))
        base = Image.alpha_composite(base, shade)
        d = ImageDraw.Draw(base)
        centered(d, "राम्रो HEADSET", 78, 56, BLUE)
        centered(d, "अब सस्तो मूल्यमा पाइन्छ", 150, 38)
        centered(d, "दमदार आवाज • स्टाइलिस डिजाइन", 1090, 30)
    elif t < 9:
        p = (t - 4) / 5
        base = product_bg.copy()
        panel = cover(product, 640, 860).convert("RGBA")
        zoom = 1 + 0.04 * p
        panel = panel.resize((round(640 * zoom), round(860 * zoom)), Image.Resampling.LANCZOS)
        base.alpha_composite(panel, ((W - panel.width) // 2, 210))
        base = Image.alpha_composite(base, Image.new("RGBA", (W, H), (0, 6, 23, 95)))
        d = ImageDraw.Draw(base)
        centered(d, "GAME होस् वा MUSIC", 65, 43)
        centered(d, "स्पष्ट साउन्ड, आरामदायी फिट", 120, 31)
        centered(d, "तपाईंको रोजाइको headset", 1090, 38, BLUE)
    else:
        p = min((t - 9) / 4, 1)
        base = Image.new("RGBA", (W, H), (4, 13, 34, 255))
        left = cover(person, 300, 670).convert("RGBA")
        right = cover(product, 380, 670).convert("RGBA")
        base.alpha_composite(left, (40, 300))
        base.alpha_composite(right, (330, 300))
        base = Image.alpha_composite(base, Image.new("RGBA", (W, H), (0, 5, 20, 80)))
        d = ImageDraw.Draw(base)
        centered(d, "राम्रो गुणस्तर", 90, 57)
        centered(d, "सस्तो मूल्य", 165, 62, BLUE)
        centered(d, "आजै उपलब्ध छ", 1030, 43)
        centered(d, "सम्पर्क गर्नुहोस्", 1100, 34)
    process.stdin.write(np.asarray(base.convert("RGB"), dtype=np.uint8).tobytes())

process.stdin.close()
if process.wait() != 0:
    raise SystemExit("ffmpeg भिडियो बनाउन असफल भयो")
print(OUT.resolve())
