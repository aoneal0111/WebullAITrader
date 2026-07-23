from pathlib import Path
from PIL import Image

source = Path("WebullAITrader.png")
output = Path("WebullAITrader.ico")

image = Image.open(source).convert("RGBA")
image.save(
    output,
    format="ICO",
    sizes=[
        (16, 16),
        (24, 24),
        (32, 32),
        (48, 48),
        (64, 64),
        (128, 128),
        (256, 256),
    ],
)

print(f"Created {output.resolve()}")
