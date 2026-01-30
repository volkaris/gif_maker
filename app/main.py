from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Literal, List

import warnings
from PIL import Image

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

app = FastAPI(title="GIF Maker", version="1.0.0")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _rgba_to_rgb(im: Image.Image, bg=(0, 0, 0)) -> Image.Image:
    if im.mode == "RGBA":
        background = Image.new("RGBA", im.size, bg + (255,))
        return Image.alpha_composite(background, im).convert("RGB")
    return im.convert("RGB")


def _resize_stretch(img: Image.Image, w: int, h: int) -> Image.Image:
    if img.size == (w, h):
        return img
    return img.resize((w, h), resample=Image.Resampling.LANCZOS)


def _resize_cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    src_w, src_h = img.size
    if src_w == 0 or src_h == 0:
        return img.resize((w, h), Image.Resampling.LANCZOS)

    scale = max(w / src_w, h / src_h)
    new_w = int(math.ceil(src_w * scale))
    new_h = int(math.ceil(src_h * scale))
    img2 = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img2.crop((left, top, left + w, top + h))


def _make_global_palette(frames_rgba: list[Image.Image]) -> Image.Image:
    w, h = frames_rgba[0].size
    thumb_w = max(1, w // 4)
    thumb_h = max(1, h // 4)

    thumbs = [f.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS) for f in frames_rgba]
    montage = Image.new("RGBA", (thumb_w * len(thumbs), thumb_h))

    x = 0
    for t in thumbs:
        montage.paste(t, (x, 0))
        x += thumb_w

    return montage.quantize(colors=256, method=Image.Quantize.FASTOCTREE)


def build_gif_bytes(
    files: list[UploadFile],
    width: int,
    height: int,
    fps: float,
    fit: Literal["stretch", "cover"] = "stretch",
) -> bytes:
    duration_ms = int(round(1000.0 / max(0.01, float(fps))))

    frames_rgba: list[Image.Image] = []
    for f in files:
        raw = f.file.read()
        img = Image.open(io.BytesIO(raw)).convert("RGBA")

        if fit == "cover":
            img = _resize_cover_crop(img, width, height)
        else:
            img = _resize_stretch(img, width, height)

        frames_rgba.append(img)

    palette_img = _make_global_palette(frames_rgba)

    frames_p: list[Image.Image] = []
    for fr in frames_rgba:
        fr_rgb = _rgba_to_rgb(fr)
        fr_p = fr_rgb.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
        frames_p.append(fr_p)

    out = io.BytesIO()
    frames_p[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=frames_p[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        optimize=False,
    )
    out.seek(0)
    return out.getvalue()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/render")
async def api_render(
    files: List[UploadFile] = File(...),
    width: int = Form(3024),
    height: int = Form(1898),
    fps: float = Form(0.5),
    fit: Literal["stretch", "cover"] = Form("stretch"),
):
    if len(files) < 1:
        return {"error": "No files"}
    if len(files) > 80:
        return {"error": "Too many files (max 80)"}
    if width <= 0 or height <= 0 or width > 7000 or height > 7000:
        return {"error": "Invalid size (max 7000x7000)"}

    for f in files:
        try:
            f.file.seek(0)
        except Exception:
            pass

    data = build_gif_bytes(files, width, height, fps, fit=fit)
    filename = f"result_{width}x{height}.gif"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="image/gif",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
