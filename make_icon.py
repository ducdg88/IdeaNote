# Vẽ icon Idea Note (tờ note vàng + bóng đèn ý tưởng) ra icon.png và icon.ico
from PIL import Image, ImageDraw

S = 512
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((16, 16, S - 16, S - 16), 110, fill=(245, 158, 11))
d.rounded_rectangle((104, 92, 408, 420), 36, fill=(255, 255, 255))
for y in (300, 346):
    d.rounded_rectangle((150, y, 362, y + 20), 10, fill=(233, 213, 176))
# bóng đèn
d.ellipse((186, 128, 326, 268), fill=(255, 196, 30))
d.rounded_rectangle((226, 250, 286, 284), 8, fill=(120, 120, 128))
d.line((256, 158, 256, 214), fill=(255, 255, 255), width=14)
d.ellipse((248, 226, 264, 242), fill=(255, 255, 255))
d.ellipse((350, 70, 430, 150), fill=(217, 54, 54))  # chấm đỏ "gấp"
im.save("icon.png")
im.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
