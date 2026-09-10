import asyncio
import os
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(title="Online Video Encoder")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

ALLOWED_RESOLUTIONS = {"original": None, "1080p": 1080, "720p": 720, "480p": 480}
ALLOWED_CODECS = {"h264": "libx264", "h265": "libx265", "vp9": "libvpx-vp9"}
ALLOWED_PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request},
    )

@app.post("/encode")
async def encode(
    file: UploadFile = File(...),
    resolution: str = Form("1080p"),
    codec: str = Form("h264"),
    crf: int = Form(23),
    preset: str = Form("medium"),
    audio_bitrate: str = Form("128k"),
):
    if not file.filename:
        raise HTTPException(400, "No file selected.")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}:
        raise HTTPException(400, "Unsupported video format.")

    if resolution not in ALLOWED_RESOLUTIONS:
        raise HTTPException(400, "Invalid resolution.")
    if codec not in ALLOWED_CODECS:
        raise HTTPException(400, "Invalid codec.")
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(400, "Invalid preset.")
    if not 0 <= crf <= 51:
        raise HTTPException(400, "CRF must be between 0 and 51.")
    if audio_bitrate not in {"96k", "128k", "160k", "192k", "256k"}:
        raise HTTPException(400, "Invalid audio bitrate.")

    job_id = uuid.uuid4().hex
    input_path = UPLOADS / f"{job_id}{suffix}"
    output_path = OUTPUTS / f"{job_id}.mp4"

    with input_path.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)

    cmd = ["ffmpeg", "-y", "-i", str(input_path)]

    height = ALLOWED_RESOLUTIONS[resolution]
    if height:
        cmd += ["-vf", f"scale=-2:{height}"]

    video_codec = ALLOWED_CODECS[codec]
    cmd += ["-c:v", video_codec, "-crf", str(crf)]

    if codec == "vp9":
        cmd += ["-b:v", "0"]
    else:
        cmd += ["-preset", preset]

    cmd += ["-c:a", "aac", "-b:a", audio_bitrate, str(output_path)]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0 or not output_path.exists():
            error = stderr.decode(errors="ignore")[-1500:]
            raise HTTPException(500, f"FFmpeg failed:\n{error}")

        download_name = f"{Path(file.filename).stem}_encoded.mp4"
        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=download_name,
            background=None,
        )
    finally:
        input_path.unlink(missing_ok=True)
        asyncio.create_task(cleanup_later(output_path))

async def cleanup_later(path: Path):
    await asyncio.sleep(300)
    path.unlink(missing_ok=True)

@app.get("/health")
async def health():
    return {"status": "ok"}
