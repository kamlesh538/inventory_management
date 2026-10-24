"""Draws the app icon (red rounded square, white 'JBH') as .ico and .png. Run once; outputs are committed."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "src" / "jbhonda" / "assets"
FONT = ASSETS / "fonts" / "DejaVuSans-Bold.ttf"


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=(204, 0, 0, 255))
    font = ImageFont.truetype(str(FONT), int(size * 0.36))
    d.text((size / 2, size / 2), "JBH", font=font, fill="white", anchor="mm")
    return img


if __name__ == "__main__":
    big = draw(256)
    big.save(ASSETS / "app.png")
    big.save(ASSETS / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", ASSETS / "app.ico")
