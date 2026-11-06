"""Address verification for regulated routing. Explicit ranges, not ipaddress's broader is_private."""

from collections.abc import Callable, Sequence
from ipaddress import IPv6Address, ip_address, ip_network
from socket import SOCK_STREAM, getaddrinfo
from urllib.parse import urlsplit

Resolver = Callable[[str], Sequence[str]]
_NETWORKS = tuple(ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))


def resolve_host(host: str) -> Sequence[str]:
    try:
        return [str(ip_address(host))]
    except ValueError:
        return [str(record[4][0]) for record in getaddrinfo(host, None, type=SOCK_STREAM)]


def _private(address: str) -> bool:
    ip = ip_address(address)
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or any(ip in network for network in _NETWORKS)


def verify_local_url(base_url: str, resolver: Resolver) -> tuple[bool, str]:
    """All returned addresses must be private. Return only the hostname for safe warning metadata."""
    host = ""
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        if not host or parsed.scheme not in {"http", "https"}:
            return False, host
        addresses = resolver(host)
        return bool(addresses) and all(_private(address) for address in addresses), host
    except (OSError, ValueError):
        return False, host
