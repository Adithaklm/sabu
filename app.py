import asyncio
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
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

ALLOWED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}
ALLOWED_RESOLUTIONS = {"original": None, "1080p": 1080, "720p": 720, "480p": 480}
ALLOWED_CODECS = {"h264": "libx264", "h265": "libx265", "vp9": "libvpx-vp9"}
ALLOWED_PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"}


def validate_settings(resolution, codec, crf, preset, audio_bitrate):
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


def build_ffmpeg(input_path, output_path, resolution, codec, crf, preset, audio_bitrate):
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
    return cmd


async def run_ffmpeg(cmd, output_path):
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0 or not output_path.exists():
        error = stderr.decode(errors="ignore")[-2000:]
        raise HTTPException(500, f"FFmpeg failed:\n{error}")


async def cleanup_later(path: Path):
    await asyncio.sleep(300)
    path.unlink(missing_ok=True)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request},
    )


@app.post("/encode")
async def encode(
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
    resolution: str = Form("1080p"),
    codec: str = Form("h264"),
    crf: int = Form(23),
    preset: str = Form("medium"),
    audio_bitrate: str = Form("128k"),
):
    validate_settings(resolution, codec, crf, preset, audio_bitrate)

    if not file and not url:
        raise HTTPException(400, "Please upload a video or enter a direct video URL.")
    if file and file.filename and url:
        raise HTTPException(400, "Choose either a local file or a URL, not both.")

    job_id = uuid.uuid4().hex
    input_path = None
    original_name = "video"

    try:
        if url:
            parsed = urlparse(url.strip())
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(400, "Enter a valid HTTP/HTTPS direct video URL.")

            path_suffix = Path(parsed.path).suffix.lower()
            suffix = path_suffix if path_suffix in ALLOWED_EXTENSIONS else ".mp4"
            original_name = Path(parsed.path).stem or "video"
            input_path = UPLOADS / f"{job_id}{suffix}"

            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
                    async with client.stream("GET", url.strip(), headers={"User-Agent": "OnlineVideoEncoder/1.0"}) as response:
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").lower()
                        if "text/html" in content_type:
                            raise HTTPException(400, "This URL points to a webpage, not a direct video file URL.")
                        with input_path.open("wb") as out:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                out.write(chunk)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, f"Could not download the video URL: {exc}")

            if not input_path.exists() or input_path.stat().st_size == 0:
                raise HTTPException(400, "The URL returned an empty file.")
        else:
            if not file or not file.filename:
                raise HTTPException(400, "No file selected.")
            suffix = Path(file.filename).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, "Unsupported video format.")
            original_name = Path(file.filename).stem
            input_path = UPLOADS / f"{job_id}{suffix}"
            with input_path.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    out.write(chunk)

        output_path = OUTPUTS / f"{job_id}.mp4"
        cmd = build_ffmpeg(input_path, output_path, resolution, codec, crf, preset, audio_bitrate)
        await run_ffmpeg(cmd, output_path)

        download_name = f"{original_name}_encoded.mp4"
        return FileResponse(output_path, media_type="video/mp4", filename=download_name)
    finally:
        if input_path:
            input_path.unlink(missing_ok=True)
        if 'output_path' in locals():
            asyncio.create_task(cleanup_later(output_path))


@app.get("/health")
async def health():
    return {"status": "ok"}
