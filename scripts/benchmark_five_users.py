"""Five isolated HTTP accounts plus opt-in real local AI. Never writes to Supabase."""
# ruff: noqa: E402 -- Script entry point adds its repository root before app imports.

import argparse
import base64
import concurrent.futures
import io
import json
import logging
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn
from dotenv import dotenv_values
from PIL import Image, ImageDraw, ImageFont

from app.api.routes import ai
from app.core.config import Settings
from app.core_cloud.writing_jobs import WritingJobs
from app.core_local import dependencies
from app.core_local.store import CoreStore
from app.main import create_app


def photo(label):
    canvas = Image.new("RGB", (800, 600), "#fff1a3")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 52)
    draw.multiline_text(
        (35, 60), f"TEA\nBUY 1 GET 1\n{label}\nPICKUP: DISCUSS", fill="black", font=font, spacing=18
    )
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-ai", action="store_true")
    parser.add_argument("--output", default=".local/five-user-after.json")
    parser.add_argument("--port", type=int, default=17367)
    parser.add_argument("--mode", choices=("both", "voice", "image"), default="both")
    parser.add_argument("--photos", choices=(1, 2, 5), type=int, default=2)
    args = parser.parse_args()
    config = dotenv_values(ROOT / ".local/local-ai.env")
    settings = Settings(
        core_local_enabled=True,
        core_supabase_enabled=False,
        environment="local",
        stealth_ai_provider="ollama_local",
        stealth_ollama_model=config["STEALTH_OLLAMA_MODEL"],
        stealth_ollama_local_only_confirmed=True,
    )
    report = {
        "scope": "isolated local password sessions; actual HTTP/API/CPU; no remote auth accounts",
        "model": settings.stealth_ollama_model,
        "photos_per_image_job": args.photos,
        "scenarios": {},
    }
    original_jobs = ai.writing_jobs
    ai.writing_jobs = WritingJobs()
    with tempfile.TemporaryDirectory(prefix="five-user-", dir=ROOT / ".local") as folder:
        store = CoreStore(Path(folder) / "test.sqlite3")
        app = create_app(settings)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        app.dependency_overrides[dependencies.get_store] = lambda: store

        @app.middleware("http")
        async def runtime(request, call_next):
            request.state.runtime_settings = settings
            return await call_next(request)

        server = uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", port=args.port, log_level="error", access_log=False
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{args.port}", timeout=30, trust_env=False
            ) as client:
                deadline = time.monotonic() + 15
                while not server.started:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Isolated API failed to start")
                    time.sleep(0.05)
                headers = []
                for index in range(5):
                    value = client.post(
                        "/api/v1/auth/register",
                        json={
                            "email": f"actor{index}@example.test",
                            "password": "Synthetic-only-123!",
                            "display_name": f"fixture{index}",
                        },
                    )
                    value.raise_for_status()
                    headers.append({"Authorization": "Bearer " + value.json()["access_token"]})
                value = client.post(
                    "/api/v1/posts",
                    headers={**headers[0], "Idempotency-Key": str(uuid4())},
                    json={
                        "content": "五人隔離測試：一起討論餐點與取餐安排，不是真實訂購。",
                        "tags": ["測試"],
                        "capacity": 5,
                        "deadline_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
                        "price_per_person": "50",
                        "location": {"name": "政大正門", "latitude": 24.987, "longitude": 121.576},
                    },
                )
                value.raise_for_status()
                post = value.json()
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    joined = list(
                        pool.map(
                            lambda h: client.post(
                                f"/api/v1/posts/{post['id']}/join-requests",
                                headers=h,
                                json={"expected_version": 1},
                            ),
                            headers[1:],
                        )
                    )
                for result in joined:
                    result.raise_for_status()
                    accepted = client.patch(
                        f"/api/v1/join-requests/{result.json()['id']}",
                        headers=headers[0],
                        json={"status": "accepted"},
                    )
                    accepted.raise_for_status()
                assert (
                    client.get(f"/api/v1/posts/{post['id']}", headers=headers[0]).json()["joined"]
                    == 5
                )
                started = time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                    reads = list(
                        pool.map(lambda h: client.get("/api/v1/posts", headers=h), headers * 10)
                    )
                assert all(r.status_code == 200 for r in reads)
                report["scenarios"]["accounts_and_membership"] = {
                    "accounts": 5,
                    "joined": 5,
                    "feed_reads": len(reads),
                    "seconds": round(time.monotonic() - started, 3),
                }
                print(json.dumps(report["scenarios"]["accounts_and_membership"]), flush=True)
                if args.real_ai:
                    modes = ("voice", "image") if args.mode == "both" else (args.mode,)
                    sources = [
                        "我想找朋友到政大正門買飲料，口味和取餐時間可以討論，還沒有決定店家和價格。",
                        "看到披薩買一送一的活動，想找人一起分攤，店家和適用品項需要再確認。",
                        "我有 2 份活動剩下的便當想免費分享，想約在政大正門交給需要的人。",
                        "我想找人一起買生活用品，願意幫忙取貨，但需要先討論品項和分攤方式。",
                        "想喝飲料，預算上限 80 元，不方便去拿，但願意另外多付 10 元請人幫忙。",
                    ]
                    for mode in modes:
                        began = time.monotonic()
                        jobs, done, latencies = [], {}, []
                        for index, h in enumerate(headers):
                            payload = {
                                "mode": mode,
                                "content": sources[index] if mode == "voice" else "",
                            }
                            if mode == "image":
                                payload["images_base64"] = [
                                    photo(f"DEMO {chr(65 + index)} {chr(65 + ordinal)}")
                                    for ordinal in range(args.photos)
                                ]
                            result = client.post(
                                "/api/v1/ai/post-writing/jobs",
                                json=payload,
                                headers={**h, "Idempotency-Key": str(uuid4())},
                            )
                            result.raise_for_status()
                            jobs.append(result.json()["id"])
                        deadline = began + 20 * 60
                        while len(done) < 5:
                            if time.monotonic() > deadline:
                                raise RuntimeError("AI benchmark timed out")
                            for index, (job, h) in enumerate(zip(jobs, headers, strict=True)):
                                if index in done:
                                    continue
                                state = client.get(f"/api/v1/ai/post-writing/jobs/{job}", headers=h)
                                state.raise_for_status()
                                data = state.json()
                                if data["status"] in ("succeeded", "failed", "cancelled"):
                                    done[index] = {
                                        "status": data["status"],
                                        "seconds": round(time.monotonic() - began, 3),
                                        "characters": len(
                                            (data.get("result") or {}).get("text", "")
                                        ),
                                        "error": data.get("error"),
                                    }
                                    print(
                                        json.dumps(
                                            {"scenario": mode, "actor": index + 1, **done[index]}
                                        ),
                                        flush=True,
                                    )
                            start_read = time.monotonic()
                            read = client.get("/api/v1/posts", headers=headers[0])
                            read.raise_for_status()
                            latencies.append(time.monotonic() - start_read)
                            if len(done) < 5:
                                time.sleep(3)
                        report["scenarios"][mode] = {
                            "results": done,
                            "seconds": round(time.monotonic() - began, 3),
                            "max_feed_seconds_during_ai": round(max(latencies), 3),
                        }
                        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps({"saved": args.output}), flush=True)
                Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            ai.writing_jobs.close()
            ai.writing_jobs = original_jobs


if __name__ == "__main__":
    main()
