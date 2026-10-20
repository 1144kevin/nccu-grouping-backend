"""A prepared, opt-in LAN TLS profile. Does not configure networks or certificates."""

from ipaddress import IPv4Address, IPv4Network

from app.core.config import Settings

NETWORKS = tuple(IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_ipv4(value: str) -> bool:
    try:
        address = IPv4Address(value)
        return any(address in network for network in NETWORKS) and str(address) == value
    except ValueError:
        return False


def validate_device_settings(settings: Settings) -> None:
    if settings.core_device_ai_enabled and (
        not settings.core_device_test_enabled
        or settings.stealth_ai_provider != "ollama_local"
        or not settings.stealth_ollama_local_only_confirmed
        or not settings.stealth_ollama_model
    ):
        raise ValueError("Device AI requires explicit device mode and confirmed local Ollama")
    if not settings.core_device_test_enabled:
        return
    if (
        settings.environment != "local"
        or not settings.core_supabase_enabled
        or settings.core_local_enabled
        or not private_ipv4(settings.core_device_test_host)
        or not 1 <= len(settings.core_device_test_clients) <= 8
        or len(set(settings.core_device_test_clients)) != len(settings.core_device_test_clients)
        or not all(private_ipv4(client) for client in settings.core_device_test_clients)
    ):
        raise ValueError(
            "Device testing requires Supabase local mode and exact private IPv4 host/clients"
        )


def device_ai_environment(profile: dict, local_ai: dict) -> dict[str, str]:
    """Fail closed; neither inherited shell settings nor a browser can opt in."""
    enabled = profile.get("DEVICE_AI_ENABLED", "false").lower()
    if enabled not in ("true", "false"):
        raise ValueError("DEVICE_AI_ENABLED must be true or false")
    values = {
        "CORE_DEVICE_AI_ENABLED": enabled,
        "STEALTH_AI_PROVIDER": "offline_rules",
        "STEALTH_SEMANTIC_ENABLED": "false",
        "STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED": "false",
        "STEALTH_OLLAMA_MODEL": "",
    }
    if enabled == "true":
        if (
            local_ai.get("STEALTH_AI_PROVIDER") != "ollama_local"
            or local_ai.get("STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED", "").lower() != "true"
            or not local_ai.get("STEALTH_OLLAMA_MODEL")
        ):
            raise ValueError("Configure confirmed local models before enabling device AI")
        for key in (
            "STEALTH_AI_PROVIDER",
            "STEALTH_OLLAMA_MODEL",
            "STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED",
            "STEALTH_SEMANTIC_ENABLED",
            "STEALTH_EMBEDDING_MODEL",
        ):
            if local_ai.get(key):
                values[key] = local_ai[key]
    return values
