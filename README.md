# Online Video Encoder

FastAPI + FFmpeg web video encoder designed for Koyeb.

## Local run

Install FFmpeg, then:

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://127.0.0.1:8000

## Koyeb

Create a new Web Service from this repository and use the included Dockerfile.

The service listens on the `PORT` environment variable.

## Important

This is a starter version. It processes one request synchronously and stores temporary files on the container filesystem. For large/public workloads, move uploads/outputs to object storage and use a queue/worker architecture.
