"""Prepare/check a normal iPhone -> Windows private HTTPS connection. No deployment."""

import argparse
import json
import shutil
import ssl
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.device_setup import (  # noqa: E402
    device_addresses,
    issue_tls,
    load_device,
    protect_directory,
)


def windows_networks():
    if sys.platform != "win32":
        return []
    script = """@(Get-NetIPConfiguration | Where-Object {
 $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up'
} | ForEach-Object { [pscustomobject]@{
 host=$_.IPv4Address.IPAddress;
 category=if([int]$_.NetProfile.NetworkCategory -eq 1){'Private'}else{'Public'};
 interface_index=$_.InterfaceIndex
} }) | ConvertTo-Json -Compress"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    value = json.loads(result.stdout or "[]")
    return value if isinstance(value, list) else [value]


def network_problem(host, networks):
    matches = [n for n in networks if n.get("host") == host]
    if not matches:
        return "Windows must currently own DEVICE_HOST on its connected Wi-Fi/hotspot"
    if not any(n.get("category") == "Private" for n in matches):
        return "Mark only the trusted personal Wi-Fi/hotspot as Private in Windows settings"
    return None


def prepare(root, host, clients, *, ai=False):
    device_addresses(host, clients)
    profile = root / ".local/device-test.env"
    if profile.exists():
        raise ValueError(
            "Existing device profile preserved; edit it explicitly rather than overwrite"
        )
    directory = root / ".local" / ("device-tls-" + uuid4().hex[:12])
    protect_directory(directory)
    issue_tls(directory, host)
    relative = directory.relative_to(root).as_posix()
    content = (
        f"DEVICE_HOST={host}\nDEVICE_CLIENTS={','.join(clients)}\n"
        f"DEVICE_AI_ENABLED={str(ai).lower()}\nDEVICE_GOOGLE_IOS_CONFIRMED=false\n"
        f"DEVICE_TLS_CERT={relative}/server.pem\nDEVICE_TLS_KEY={relative}/server-key.pem\n"
        f"DEVICE_TLS_CA={relative}/ca.pem\n"
    )
    with profile.open("x", encoding="utf8") as target:
        target.write(content)
    profile.chmod(0o600)
    return export_public(root)


def export_public(root):
    settings, paths, tls = load_device(root)
    target = root / ".local/iphone-windows-link"
    target.mkdir(parents=True, exist_ok=True)
    # Explicit allowlist: never copy the profile, leaf private key or any env file.
    shutil.copyfile(paths[2].with_name("ca.cer"), target / "ca.cer")
    public = {
        "format": "nccu-device-link-v1",
        "api": f"https://{settings.core_device_test_host}:8443/api/v1",
        "google_callback": "nccugrouping://auth/callback",
        "ca_file": "ca.cer",
        "ca_sha256": tls["ca_sha256"],
        "leaf_expires": tls["leaf_expires"],
    }
    (target / "device-connection.json").write_text(
        json.dumps(public, indent=2) + "\n", encoding="utf8"
    )
    return public


def check(root):
    settings, _, tls = load_device(root)
    problems = []
    if sys.platform == "win32":
        problem = network_problem(settings.core_device_test_host, windows_networks())
        if problem:
            problems.append(problem)
    if not settings.core_google_ios_enabled:
        problems.append("Confirm Supabase Auth exact native redirect before enabling Google iOS")
    return {
        "status": "needs_setup" if problems else "configured",
        "api": f"https://{settings.core_device_test_host}:8443/api/v1",
        "tls": tls,
        "google_ios_enabled": settings.core_google_ios_enabled,
        "backend_ai": settings.stealth_ai_provider,
        "semantic_enabled": settings.stealth_semantic_enabled,
        "images_enabled": settings.core_media_enabled,
        "problems": problems,
        "iphone_trust_and_connectivity": "not_verified",
        "deployed": False,
    }


def probe(root):
    import httpx

    settings, (_, _, ca), _ = load_device(root)
    context = ssl.create_default_context(cafile=str(ca))
    with httpx.Client(verify=context, trust_env=False, timeout=5, follow_redirects=False) as client:
        response = client.get(f"https://{settings.core_device_test_host}:8443/health")
    return {
        "tls_verified": True,
        "health_http": response.status_code,
        "windows_source_may_be_denied": response.status_code == 403,
        "iphone_connectivity": "requires Safari/App on the actual iPhone",
    }


def firewall_command(root, action):
    if sys.platform != "win32":
        raise ValueError("Firewall helper is Windows-only")
    name = "NCCUGrouping-PrivateDevice-8443"
    prefix = "$ErrorActionPreference='Stop'; "
    if action == "check":
        return (
            prefix + f"Get-NetFirewallRule -Name '{name}' -ErrorAction SilentlyContinue | "
            "Select-Object Name,Enabled,Profile,Direction,Action | ConvertTo-Json"
        )
    if action == "remove":
        return prefix + f"Remove-NetFirewallRule -Name '{name}'"
    settings, _, _ = load_device(root)
    host, clients = settings.core_device_test_host, settings.core_device_test_clients
    device_addresses(host, clients)
    problem = network_problem(host, windows_networks())
    if problem:
        raise ValueError(problem)
    # Values interpolated below are already validated exact IPv4 addresses only.
    remote = ",".join(f"'{c}'" for c in clients)
    return (
        prefix + f"if (Get-NetFirewallRule -Name '{name}' -ErrorAction SilentlyContinue) "
        "{ throw 'Existing named rule preserved; remove it explicitly before replacing' }; "
        f"New-NetFirewallRule -Name '{name}' -DisplayName 'NCCU private iPhone test' "
        f"-Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -LocalAddress '{host}' "
        f"-RemoteAddress @({remote}) -Profile Private -EdgeTraversalPolicy Block | Out-Null"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "networks",
            "prepare",
            "check",
            "export",
            "confirm-google",
            "firewall-check",
            "firewall-add",
            "firewall-remove",
            "probe",
        ),
    )
    parser.add_argument("--host")
    parser.add_argument("--clients", help="Comma-separated exact private iPhone IPv4 addresses")
    parser.add_argument("--ai", action="store_true", help="Use existing confirmed Windows Ollama")
    args = parser.parse_args()
    if args.action == "networks":
        result = windows_networks()
    elif args.action == "prepare":
        clients = [c.strip() for c in (args.clients or "").split(",") if c.strip()]
        if sys.platform == "win32":
            if not any(n.get("host") == args.host for n in windows_networks()):
                raise ValueError("Choose the current Windows private IPv4, not an example address")
        result = prepare(ROOT, args.host or "", clients, ai=args.ai)
    elif args.action == "export":
        result = export_public(ROOT)
    elif args.action == "probe":
        result = probe(ROOT)
    elif args.action == "confirm-google":
        # Explicit human acknowledgement; never claims Management API verification.
        path = ROOT / ".local/device-test.env"
        load_device(ROOT)
        content = path.read_text(encoding="utf8")
        if "DEVICE_GOOGLE_IOS_CONFIRMED=" in content:
            content = (
                "\n".join(
                    "DEVICE_GOOGLE_IOS_CONFIRMED=true"
                    if line.startswith("DEVICE_GOOGLE_IOS_CONFIRMED=")
                    else line
                    for line in content.splitlines()
                )
                + "\n"
            )
        else:
            content += "\nDEVICE_GOOGLE_IOS_CONFIRMED=true\n"
        path.write_text(content, encoding="utf8")
        result = {"google_ios": "user_confirmed_exact_allowlist", "real_login": "not_verified"}
    elif args.action.startswith("firewall-"):
        action = args.action.removeprefix("firewall-")
        # Mutations require a normal Windows elevated terminal; no UAC bypass or policy change.
        script = firewall_command(ROOT, action)
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode:
            raise ValueError(
                "Firewall action failed. Use an Administrator terminal for add/remove; "
                "existing rules are preserved. No global firewall settings changed."
            )
        result = {
            "firewall_action": action,
            "scope": "Private network, exact host/clients, TCP8443",
            "detail": r.stdout.strip(),
        }
    else:
        result = check(ROOT)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
