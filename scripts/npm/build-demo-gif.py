"""Assemble captured product views; requires Pillow. No generated UI artwork."""
from pathlib import Path
from PIL import Image
root = Path(__file__).resolve().parents[2] / 'docs/assets/workspace'
frames = []
for name in ['threads', 'review', 'handoff', 'architecture']:
    with Image.open(root / f'{name}.png') as image:
        frame = image.convert('RGB').resize((1080, 840), Image.Resampling.LANCZOS)
        frames.append(frame.quantize(colors=192, method=Image.Quantize.MEDIANCUT))
frames[0].save(root / 'workflow.gif', save_all=True, append_images=frames[1:], duration=[2400, 3200, 2800, 3000], loop=0, optimize=True, disposal=2)
print(f'Wrote {root / "workflow.gif"}')
