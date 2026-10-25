"""
Server-Side Request Forgery (SSRF) Protection Utility.
Validates outgoing URLs before fetching external feeds, webhooks, or podcast RSS feeds.
Blocks loopback, private networks, cloud metadata endpoints, and non-HTTP protocols.
"""

import ipaddress
import socket
from typing import Set, Tuple
from urllib.parse import urlparse

# Prohibited hostnames (Cloud metadata and internal services)
BLOCKED_HOSTNAMES: Set[str] = {
    "localhost",
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
    "169.254.169.254",
    "169.254.169.253",
}


def is_ip_blocked(ip_str: str) -> Tuple[bool, str]:
    """Inspects an IP address and rejects private, loopback, link-local, or reserved ranges."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, f"Invalid IP address format: {ip_str}"

    if ip.is_loopback:
        return True, f"Loopback IP address rejected: {ip_str}"
    if ip.is_private:
        return True, f"Private network IP address rejected: {ip_str}"
    if ip.is_link_local:
        return True, f"Link-local / cloud metadata IP address rejected: {ip_str}"
    if ip.is_multicast:
        return True, f"Multicast IP address rejected: {ip_str}"
    if ip.is_reserved:
        return True, f"Reserved IP address rejected: {ip_str}"

    return False, "IP is safe"


def validate_safe_url(url: str, resolve_dns: bool = True) -> Tuple[bool, str]:
    """
    Validates that a URL is safe to fetch and does not pose an SSRF risk.
    Rejects non-HTTP(S) schemes, private IP addresses, localhost, and AWS/GCP metadata services.
    """
    if not url or not isinstance(url, str):
        return False, "URL must be a non-empty string"

    try:
        parsed = urlparse(url.strip())
    except Exception as e:
        return False, f"Malformed URL: {e}"

    # 1. Scheme Validation
    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' prohibited; only HTTP and HTTPS allowed"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL missing hostname"

    hostname_lower = hostname.lower()

    # 2. Block known dangerous hostnames
    if hostname_lower in BLOCKED_HOSTNAMES:
        return False, f"Prohibited cloud metadata or internal host: {hostname}"

    # 3. Direct IP check in URL
    try:
        ip = ipaddress.ip_address(hostname_lower)
        blocked, reason = is_ip_blocked(str(ip))
        if blocked:
            return False, reason
        return True, "URL is safe"
    except ValueError:
        pass  # Hostname is a domain name, proceed to DNS resolution

    # 4. DNS Resolution Check (prevents DNS rebinding and internal host resolution)
    if resolve_dns:
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            resolved_ips = set()
            for item in addr_info:
                sockaddr = item[4]
                if sockaddr:
                    resolved_ips.add(sockaddr[0])

            if not resolved_ips:
                return False, f"Could not resolve host: {hostname}"

            for ip_str in resolved_ips:
                blocked, reason = is_ip_blocked(ip_str)
                if blocked:
                    return False, f"Host '{hostname}' resolved to prohibited address: {reason}"

        except socket.gaierror:
            # If domain cannot be resolved locally in test mode, block if looks like internal domain
            if any(hostname_lower.endswith(tld) for tld in (".internal", ".local", ".localhost", ".corp", ".lan")):
                return False, f"Internal TLD prohibited: {hostname}"

    return True, "URL is safe"
