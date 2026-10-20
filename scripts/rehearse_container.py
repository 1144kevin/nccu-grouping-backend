"""Check a LOCAL Linux Docker engine; opt-in image build and isolated offline smoke.

No remote daemon, push, ports, host mounts, cloud credentials or privileged container.
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".local" / "container-rehearsal.json"
FAILURE_LOG = ROOT / ".local" / "container-command-failure.log"

# Synthetic public configuration only; never inherit host credentials into containers.
RUNTIME_ENV = {
    "ENVIRONMENT": "cloud",
    "CORE_CLOUD_ENABLED": "true",
    "CORE_SUPABASE_ENABLED": "true",
    "CLOUD_PUBLIC_API_URL": "https://api.team-domain.org/api/v1",
    "CLOUD_PROXY_NETWORKS": '["127.0.0.1/32"]',
    "BACKEND_CORS_ORIGINS": "[]",
    "SUPABASE_URL": "https://axdwbfhrkbovgrbngrrh.supabase.co",
    "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_synthetic_rehearsal_key",
}

HTTP_PROBE = """
import json,urllib.request,urllib.error
headers={'Host':'api.team-domain.org','X-Forwarded-Proto':'https',
 'X-Forwarded-For':'198.51.100.8'}
def status(path):
 try:
  return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
   urllib.request.Request('http://127.0.0.1:8000'+path,headers=headers),timeout=3).status
 except urllib.error.HTTPError as e:
  return e.code
assert status('/health')==200
assert status('/api/v1/posts')==401
print(json.dumps({'health':200,'anonymous_posts':401}))
"""

SMOKE = """
import asyncio,importlib,json,os,platform
from pathlib import Path
import httpx
from app.core.config import Settings
from app.core.cloud_runtime import validate_cloud_settings
from app.main import create_app
assert os.getuid()==10001
assert not any(Path('/srv/app').glob('.local'))
assert not Path('/srv/app/.env').exists()
native_modules=['asyncpg','PIL._imaging','cryptography.hazmat.bindings._rust',
 'pydantic_core._pydantic_core','uvloop','httptools']
for name in native_modules:
 importlib.import_module(name)
s=Settings(_env_file=None,environment='cloud',core_cloud_enabled=True,
 core_supabase_enabled=True,cloud_public_api_url='https://api.team-domain.org/api/v1',
 cloud_proxy_networks=['127.0.0.1/32'],backend_cors_origins=[],
 supabase_url='https://axdwbfhrkbovgrbngrrh.supabase.co',
 supabase_publishable_key='sb_publishable_synthetic_rehearsal_key')
validate_cloud_settings(s)
async def run():
 transport=httpx.ASGITransport(app=create_app(s),client=('127.0.0.1',1234))
 headers={'x-forwarded-proto':'https','x-forwarded-for':'198.51.100.8'}
 async with httpx.AsyncClient(transport=transport,
  base_url='http://api.team-domain.org',headers=headers) as c:
  assert (await c.get('/health')).status_code==200
  assert (await c.get('/api/v1/posts')).status_code==401
asyncio.run(run())
print(json.dumps({'uid':os.getuid(),'health':200,'anonymous_posts':401,'network':'none',
 'architecture':platform.machine(),'python':platform.python_version(),
 'native_modules':native_modules}))
"""


def local_endpoint(endpoint):
    return endpoint in (
        "unix:///var/run/docker.sock",
        "npipe:////./pipe/docker_engine",
        "npipe:////./pipe/dockerDesktopLinuxEngine",
    ) or (endpoint.startswith("unix:///") and endpoint.endswith("/.docker/run/docker.sock"))


def execute(args, timeout=30):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DOCKER_HOST", "DOCKER_CONTEXT", "BUILDKIT_HOST", "BUILDX_BUILDER")
    }
    result = subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        # Do not print a full environment, daemon context or registry credentials.
        FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        FAILURE_LOG.write_text(result.stdout + "\n" + result.stderr, encoding="utf8")
        raise RuntimeError(
            f"Container command failed with status {result.returncode}; see ignored failure log"
        )
    return result.stdout.strip()


def runtime_flags(container):
    return [
        "--name",
        container,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
    ]


def verify_lifecycle(docker, image, container):
    environment = [item for k, v in RUNTIME_ENV.items() for item in ("--env", f"{k}={v}")]
    execute(
        docker
        + ["run", "--detach"]
        + runtime_flags(container)
        + [
            "--health-interval=1s",
            "--health-start-period=1s",
            "--health-timeout=3s",
            "--health-retries=5",
        ]
        + environment
        + [image]
    )
    deadline = time.monotonic() + 45
    while True:
        state = json.loads(execute(docker + ["inspect", "--format", "{{json .State}}", container]))
        if not state["Running"] or state.get("Health", {}).get("Status") == "unhealthy":
            raise RuntimeError("Native container startup/healthcheck failed")
        if state.get("Health", {}).get("Status") == "healthy":
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Native container healthcheck deadline exceeded")
        time.sleep(1)
    probe = json.loads(execute(docker + ["exec", container, "python", "-c", HTTP_PROBE]))
    execute(docker + ["stop", "--time", "10", container], timeout=20)
    state = json.loads(execute(docker + ["inspect", "--format", "{{json .State}}", container]))
    if state["Running"] or state["ExitCode"] != 0 or state["OOMKilled"]:
        raise RuntimeError("Container did not shut down cleanly on SIGTERM")
    return {**probe, "docker_healthcheck": "healthy", "sigterm_exit": 0, "oom_killed": False}


def run(build=False):
    cli = shutil.which("docker")
    report = {"status": "blocked", "built": False, "deployed": False, "pushed": False}
    if not cli:
        report["reason"] = "Docker CLI/engine is not installed"
    else:
        docker = [cli, "--context", "default"]
        try:
            endpoint = execute(
                docker + ["context", "inspect", "default", "--format", "{{.Endpoints.docker.Host}}"]
            )
            if not local_endpoint(endpoint):
                raise ValueError("Only a local Docker engine is allowed")
            if execute(docker + ["info", "--format", "{{.OSType}}"]) != "linux":
                raise ValueError("A running Linux container engine is required")
            report.update(status="ready", reason="Local Linux engine available")
            if build:
                suffix = uuid4().hex[:12]
                image = "nccu-grouping-rehearsal:" + suffix
                container = "nccu-rehearsal-" + suffix
                built = False
                try:
                    execute(
                        docker
                        + ["buildx", "build", "--builder", "default", "--load", "-t", image, "."],
                        timeout=900,
                    )
                    built = True
                    report["built"] = True
                    smoke = execute(
                        docker
                        + [
                            "run",
                            "--rm",
                        ]
                        + runtime_flags(container)
                        + [
                            "--entrypoint",
                            "python",
                            image,
                            "-c",
                            SMOKE,
                        ],
                        timeout=60,
                    )
                    lifecycle = verify_lifecycle(docker, image, container)
                    report.update(
                        status="passed", built=True, smoke=json.loads(smoke), lifecycle=lifecycle
                    )
                finally:
                    # Only this unique rehearsal image/container, never prune or remove volumes.
                    subprocess.run(
                        docker + ["rm", "-f", container], capture_output=True, timeout=15
                    )
                    if built:
                        execute(docker + ["image", "rm", image])
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            report.update(status="blocked", reason=str(error))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build", action="store_true", help="Build and run only after local engine checks"
    )
    value = run(parser.parse_args().build)
    print(json.dumps(value, indent=2))
    raise SystemExit(2 if value["status"] == "blocked" else 0)
