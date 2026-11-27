from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "assets" / "corneteros_cs_source.png"
BLACK_LOGO = ROOT / "logo_cs_fondo_negro.png"
TRANSPARENT_LOGO = ROOT / "logo_cs_transparente.png"
INSTALLER_DIR = ROOT / "installer"


def crop_source():
    image = Image.open(SOURCE).convert("RGB")
    visible = image.convert("L").point(lambda value: 255 if value > 8 else 0)
    bbox = visible.getbbox()
    if not bbox:
        raise SystemExit("No se encontró el monograma claro en la imagen de origen.")
    left, top, right, bottom = bbox
    padding = 42
    bbox = (
        max(0, left - padding), max(0, top - padding),
        min(image.width, right + padding), min(image.height, bottom + padding),
    )
    return image.crop(bbox)


def create_logo_variants():
    source = crop_source()
    source.save(BLACK_LOGO, optimize=True)

    luminance = source.convert("L")
    alpha = luminance.point(lambda value: max(0, min(255, round((value - 5) * 255 / 250))))
    transparent = Image.new("RGBA", source.size, "#ffffff")
    transparent.putalpha(alpha)
    transparent.save(TRANSPARENT_LOGO, optimize=True)


def create_app_icon():
    logo = Image.open(TRANSPARENT_LOGO).convert("RGBA")
    bbox = logo.getchannel("A").getbbox()
    if not bbox:
        raise SystemExit("El logo transparente quedó vacío.")
    logo = logo.crop(bbox)

    size = 512
    diameter = 468
    max_width = 420
    max_height = 300
    scale = min(max_width / logo.width, max_height / logo.height)
    resized = logo.resize(
        (max(1, round(logo.width * scale)), max(1, round(logo.height * scale))),
        getattr(Image, "Resampling", Image).LANCZOS,
    )
    badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    bounds = ((size - diameter) // 2,) * 2 + ((size + diameter) // 2,) * 2
    draw.ellipse(bounds, fill="#101012", outline="#77777c", width=7)
    position = ((size - resized.width) // 2, (size - resized.height) // 2)
    badge.alpha_composite(resized, position)
    badge.save(ROOT / "app_icon.png", format="PNG", optimize=True)
    badge.save(
        ROOT / "app.ico", format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )


def create_installer_artwork():
    """Genera paneles BMP compatibles con el asistente moderno de Inno Setup."""
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    logo = Image.open(TRANSPARENT_LOGO).convert("RGBA")

    panel = Image.new("RGB", (164, 314), "#111114")
    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, 0, 163, 5), fill="#77777c")
    draw.ellipse((24, 32, 140, 148), fill="#18181b", outline="#5e5e65", width=2)
    logo.thumbnail((96, 70), getattr(Image, "Resampling", Image).LANCZOS)
    panel_rgba = panel.convert("RGBA")
    panel_rgba.alpha_composite(logo, ((164 - logo.width) // 2, 55))
    panel = panel_rgba.convert("RGB")
    draw = ImageDraw.Draw(panel)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 11)
    except OSError:
        font = ImageFont.load_default()
    label = "CORNETEROS\nSTUDIO"
    bounds = draw.multiline_textbbox((0, 0), label, font=font, spacing=4, align="center")
    draw.multiline_text(
        ((164 - (bounds[2] - bounds[0])) // 2, 178), label,
        font=font, fill="#f4f4f5", spacing=4, align="center"
    )
    draw.line((42, 229, 122, 229), fill="#68686e", width=2)
    draw.arc((43, 245, 121, 279), start=190, end=350, fill="#96969c", width=2)
    panel.save(INSTALLER_DIR / "wizard.bmp", format="BMP")

    small = Image.new("RGB", (55, 55), "#111114")
    small_draw = ImageDraw.Draw(small)
    small_draw.ellipse((2, 2, 52, 52), fill="#18181b", outline="#77777c", width=1)
    small_rgba = small.convert("RGBA")
    small_logo = Image.open(TRANSPARENT_LOGO).convert("RGBA")
    small_logo.thumbnail((38, 28), getattr(Image, "Resampling", Image).LANCZOS)
    small_rgba.alpha_composite(
        small_logo, ((55 - small_logo.width) // 2, (55 - small_logo.height) // 2)
    )
    small_rgba.convert("RGB").save(INSTALLER_DIR / "wizard_small.bmp", format="BMP")

def main():
    if not SOURCE.is_file():
        raise SystemExit("Falta assets/corneteros_cs_source.png.")
    create_logo_variants()
    create_app_icon()
    create_installer_artwork()
    print("Logo CS listo para la app y el instalador personalizado.")


if __name__ == "__main__":
    main()
