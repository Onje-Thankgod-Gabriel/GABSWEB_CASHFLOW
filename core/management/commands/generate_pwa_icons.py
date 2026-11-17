"""Generate PWA icon assets from the approved Gabsweb CashFlow brand mark.

The source artwork is a white logo on a transparent canvas. Brand icons are
rendered on an opaque indigo-violet canvas for launcher and in-app surfaces.

The standard 512px ``any`` icon is the WebAPK splash icon. It follows the
dashboard brand mark: a white logo on the indigo-violet gradient with rounded
corners with transparent outside pixels so the tile reads as rounded on the
native splash and on desktop mounts. A 1024px variant is included so
high-density splashes and install surfaces have enough resolution to stay sharp.

The maskable icons feed the Android adaptive launcher and the native Chrome
install dialog. They keep the indigo-violet canvas and white mark so the
launcher icon and installation modal do not change. Run after replacing the
approved source artwork:

    python manage.py generate_pwa_icons
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageDraw

START_COLOR = (99, 102, 241)
END_COLOR = (139, 92, 246)
GRADIENT_SIZE = 1024

# Must equal the manifest ``background_color`` so the splash icon canvas is
# indistinguishable from the native Android launch splash background.
SPLASH_BACKGROUND = (12, 13, 17)

# The rounded splash tile is inset inside the icon canvas so the native
# splash shows a smaller, balanced logo within its fixed OS-owned slot.
SPLASH_TILE_RATIO = 0.82

# name, canvas size, logo width ratio, background kind, mark kind
ICON_SPECS = [
    ("icon-192.png", 192, 0.58, "brand", "white"),
    ("icon-512.png", 512, 0.34, "brand_rounded", "white"),
    ("icon-1024.png", 1024, 0.34, "brand_rounded", "white"),
    ("icon-maskable-192.png", 192, 0.60, "brand", "white"),
    ("icon-maskable-512.png", 512, 0.60, "brand", "white"),
    ("apple-touch-icon.png", 180, 0.58, "brand", "white"),
    ("favicon-32.png", 32, 0.72, "brand", "white"),
    ("favicon-16.png", 16, 0.72, "brand", "white"),
]


def build_gradient(size):
    """Return a diagonal brand gradient with no drawn borders."""
    canvas = Image.new("RGB", (size, size))
    pixels = canvas.load()
    denominator = max(1, (size - 1) * 2)
    for y in range(size):
        for x in range(size):
            ratio = (x + y) / denominator
            pixels[x, y] = tuple(
                round(start + (end - start) * ratio)
                for start, end in zip(START_COLOR, END_COLOR)
            )
    return canvas


def build_background(kind, size, gradient):
    if kind == "splash":
        return Image.new("RGB", (size, size), SPLASH_BACKGROUND)
    if kind == "brand_rounded":
        return build_rounded_brand(size, gradient)
    return gradient.resize((size, size), Image.LANCZOS)


def build_rounded_brand(size, gradient):
    """Return a rounded brand tile with splash-coloured corners.

    The corners match the native splash background, so the tile reads as a
    rounded logo without transparency (which mobile platforms flatten).
    Drawing at 4x and downsampling keeps the corner easing crisp.
    """
    scale = 4
    canvas_size = size * scale
    tile_size = max(1, round(canvas_size * SPLASH_TILE_RATIO))
    offset = (canvas_size - tile_size) // 2
    tile = gradient.resize((tile_size, tile_size), Image.LANCZOS)
    mask = Image.new("L", (tile_size, tile_size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (0, 0, tile_size - 1, tile_size - 1),
        radius=round(tile_size * 0.28),
        fill=255,
    )
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    canvas.paste(tile.convert("RGBA"), (offset, offset), mask)
    return canvas.resize((size, size), Image.LANCZOS)


def render_icon(gradient, logo_alpha, canvas_size, logo_scale, background_kind, mark_kind):
    background = build_background(background_kind, canvas_size, gradient)
    logo_width = max(1, round(canvas_size * logo_scale))
    logo_height = max(
        1, round(logo_width * logo_alpha.height / logo_alpha.width)
    )
    if logo_height > round(canvas_size * 0.8):
        logo_height = round(canvas_size * 0.8)
        logo_width = max(
            1, round(logo_height * logo_alpha.width / logo_alpha.height)
        )

    alpha = logo_alpha.resize((logo_width, logo_height), Image.LANCZOS)
    if mark_kind == "gradient":
        mark = gradient.resize((logo_width, logo_height), Image.LANCZOS)
        mark = mark.convert("RGBA")
        mark.putalpha(alpha)
    else:
        mark = Image.new("RGBA", alpha.size, (255, 255, 255, 0))
        mark.putalpha(alpha)

    offset = (
        (canvas_size - logo_width) // 2,
        (canvas_size - logo_height) // 2,
    )
    has_transparency = background.mode == "RGBA"
    background = background.convert("RGBA")
    background.alpha_composite(mark, dest=offset)
    return background if has_transparency else background.convert("RGB")


class Command(BaseCommand):
    help = "Regenerate PWA icons from the approved brand logo artwork."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=str(
                settings.BASE_DIR
                / "static"
                / "assets"
                / "images"
                / "gabsweb-cashflow-logo.png"
            ),
            help="Path to the approved source logo (white mark, transparent).",
        )
        parser.add_argument(
            "--output",
            default=str(settings.BASE_DIR / "static" / "assets" / "icons"),
            help="Directory that receives the generated icon files.",
        )

    def handle(self, *args, **options):
        source_path = Path(options["source"])
        output_path = Path(options["output"])

        if not source_path.is_file():
            raise CommandError(f"Source logo not found: {source_path}")

        output_path.mkdir(parents=True, exist_ok=True)

        with Image.open(source_path) as source:
            source = source.convert("RGBA")
            alpha = source.getchannel("A")
            bounds = alpha.getbbox()
            if not bounds:
                raise CommandError("The source logo has no visible pixels.")
            logo_alpha = alpha.crop(bounds)

        gradient = build_gradient(GRADIENT_SIZE)

        for name, size, scale, background_kind, mark_kind in ICON_SPECS:
            icon = render_icon(
                gradient, logo_alpha, size, scale, background_kind, mark_kind
            )
            destination = output_path / name
            icon.save(destination, format="PNG", optimize=True)
            self.stdout.write(f"Wrote {destination.relative_to(settings.BASE_DIR)}")

        self.stdout.write(
            self.style.SUCCESS("PWA icons regenerated from the approved brand mark.")
        )
