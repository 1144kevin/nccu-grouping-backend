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
