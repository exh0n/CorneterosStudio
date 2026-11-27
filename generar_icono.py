from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
SIZE = 512
SCALE = 4
LARGE = SIZE * SCALE


def main():
    image = Image.new("RGBA", (LARGE, LARGE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = lambda value: int(round(value * SCALE))

    draw.rounded_rectangle(
        (scale(12), scale(12), scale(500), scale(500)),
        radius=scale(112), fill="#0b0d12", outline="#343840", width=scale(4),
    )

    # Silueta espectral construida con una forma de onda en el cuerpo.
    draw.ellipse((scale(174), scale(162), scale(338), scale(326)), fill="#eceef3")
    draw.rectangle((scale(174), scale(244), scale(338), scale(306)), fill="#eceef3")
    draw.polygon(
        [(scale(x), scale(y)) for x, y in (
            (174, 288), (201, 307), (228, 288), (256, 308),
            (284, 288), (311, 307), (338, 288), (338, 326), (174, 326),
        )], fill="#eceef3",
    )
    for x, top, bottom in ((218, 232, 285), (243, 213, 304), (269, 225, 293), (294, 207, 315)):
        draw.line((scale(x), scale(top), scale(x), scale(bottom)), fill="#262a33", width=scale(13))
        radius = scale(7)
        draw.ellipse((scale(x)-radius, scale(top)-radius, scale(x)+radius, scale(top)+radius), fill="#262a33")
        draw.ellipse((scale(x)-radius, scale(bottom)-radius, scale(x)+radius, scale(bottom)+radius), fill="#262a33")

    draw.ellipse(
        (scale(76), scale(76), scale(436), scale(436)),
        outline="#e64f61", width=scale(22),
    )
    draw.line(
        (scale(128), scale(384), scale(384), scale(128)),
        fill="#e64f61", width=scale(25),
    )

    resampling = getattr(Image, "Resampling", Image).LANCZOS
    icon = image.resize((SIZE, SIZE), resampling)
    icon.save(ROOT / "app_icon.png", format="PNG", optimize=True)
    icon.resize((256, 256), resampling).save(
        ROOT / "app.ico", format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print("Iconos listos: app_icon.png y app.ico")


if __name__ == "__main__":
    main()
