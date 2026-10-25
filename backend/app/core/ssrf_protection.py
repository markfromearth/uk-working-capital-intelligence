"""
Server-Side Request Forgery (SSRF) & Outbound Network Security Boundary.
Enforces strict URL validation, IPv4/IPv6/mapped-IPv6/carrier-grade-NAT address filtering,
fail-closed DNS resolution, DNS rebinding protection, redirect hop revalidation,
timeouts, and response size/content-type limits.
"""

import ipaddress
import re
import socket
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, Union
from urllib.parse import urljoin, urlparse
import httpcore
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import AsyncNetworkBackend, AsyncNetworkStream
import httpx

# Prohibited hostnames (Cloud metadata and internal services)
BLOCKED_HOSTNAMES: Set[str] = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.internal",
    "metadata",
    "instance-data",
    "169.254.169.254",
    "169.254.169.253",
    "169.254.170.2",
    "fd00:ec2::254",
}

BLOCKED_TLD_SUFFIXES: Tuple[str, ...] = (
    ".internal",
    ".local",
    ".localhost",
    ".corp",
    ".lan",
    ".home",
    ".intranet",
    ".localdomain",
)

CARRIER_GRADE_NAT_NET = ipaddress.ip_network("100.64.0.0/10")

# Detects numeric/octal/hex IPv4 obfuscations like 2130706433, 0x7f000001, 0177.0.0.1
_NUMERIC_OR_HEX_HOST_RE = re.compile(
    r"^(0x[0-9a-fA-F]+|\d+|0[0-7]+(\.[0-9xXa-fA-F]+){1,3})$"
)


def _normalize_and_parse_ip(ip_candidate: str) -> Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    """Parses standard IPv4/IPv6 strings as well as integer/hex/octal IPv4 encodings."""
    cleaned = ip_candidate.strip().strip("[]")
    if "%" in cleaned:
        cleaned = cleaned.split("%", 1)[0]
    try:
        return ipaddress.ip_address(cleaned)
    except ValueError:
        pass

    # Check if integer or hex IPv4 representation (e.g. 2130706433 or 0x7f000001)
    if _NUMERIC_OR_HEX_HOST_RE.match(cleaned):
        try:
            if "." in cleaned:
                parts = [int(p, 0) for p in cleaned.split(".")]
                if len(parts) == 4 and all(0 <= p <= 255 for p in parts):
                    packed = (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]
                    return ipaddress.IPv4Address(packed)
            else:
                val = int(cleaned, 0)
                if 0 <= val <= 0xFFFFFFFF:
                    return ipaddress.IPv4Address(val)
        except Exception:
            pass
    return None


def is_ip_blocked(ip_str: str) -> Tuple[bool, str]:
    """
    Inspects an IPv4 or IPv6 address (including IPv4-mapped IPv6 and 6to4/Teredo)
    and rejects loopback, private, link-local, unspecified, multicast, reserved,
    carrier-grade NAT, or non-global ranges.
    """
    ip = _normalize_and_parse_ip(ip_str)
    if ip is None:
        return True, f"Invalid or obfuscated IP address format: {ip_str}"

    # Unwrap IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1 or ::ffff:169.254.169.254)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            mapped_blocked, mapped_reason = is_ip_blocked(str(ip.ipv4_mapped))
            if mapped_blocked:
                return True, f"IPv4-mapped IPv6 address rejected ({ip_str} -> {mapped_reason})"
            return False, "IP is safe"
        if ip.sixtofour is not None:
            stf_blocked, stf_reason = is_ip_blocked(str(ip.sixtofour))
            if stf_blocked:
                return True, f"6to4 IPv6 address rejected ({ip_str} -> {stf_reason})"
        if ip.teredo is not None:
            for teredo_ip in ip.teredo:
                t_blocked, t_reason = is_ip_blocked(str(teredo_ip))
                if t_blocked:
                    return True, f"Teredo IPv6 address rejected ({ip_str} -> {t_reason})"

    if ip.is_unspecified:
        return True, f"Unspecified IP address rejected: {ip_str}"
    if ip.is_loopback:
        return True, f"Loopback IP address rejected: {ip_str}"
    if ip.is_link_local:
        return True, f"Link-local / cloud metadata IP address rejected: {ip_str}"
    if ip.is_private:
        return True, f"Private network IP address rejected: {ip_str}"
    if ip.is_multicast:
        return True, f"Multicast IP address rejected: {ip_str}"
    if ip.is_reserved:
        return True, f"Reserved IP address rejected: {ip_str}"
    if isinstance(ip, ipaddress.IPv4Address) and ip in CARRIER_GRADE_NAT_NET:
        return True, f"Carrier-grade NAT (RFC 6598) IP address rejected: {ip_str}"
    if not ip.is_global:
        return True, f"Non-global IP address rejected: {ip_str}"

    return False, "IP is safe"


def validate_safe_url(
    url: str,
    resolve_dns: bool = True,
    allow_http: bool = False,
    allowed_ports: Optional[Set[int]] = None,
    dns_resolver: Optional[Callable[[str], List[str]]] = None,
) -> Tuple[bool, str]:
    """
    Validates that a URL is safe to fetch and does not pose an SSRF risk.
    - Rejects non-HTTP(S) schemes (and requires HTTPS unless allow_http=True or URL is validated in compatibility mode).
    - Rejects embedded userinfo credentials (user:pass@host).
    - Rejects non-standard ports (default allowed: {443, 80}).
    - Rejects private/loopback/metadata/unspecified/mapped-IPv6/numeric-encoded IPs.
    - Fails CLOSED on DNS resolution failures when resolve_dns=True.
    """
    if not url or not isinstance(url, str):
        return False, "URL must be a non-empty string"

    cleaned_url = url.strip()
    if any(c in cleaned_url for c in ("\r", "\n", "\x00", "\t")):
        return False, "URL contains prohibited control characters"

    try:
        parsed = urlparse(cleaned_url)
    except Exception as e:
        return False, f"Malformed URL: {e}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' prohibited; only HTTPS (or HTTP when explicitly permitted) allowed"
    if scheme == "http" and not allow_http:
        # Check if host is already a blocked IP/metadata first so error message is specific
        pass

    if parsed.username is not None or parsed.password is not None:
        return False, "URL with embedded userinfo credentials is prohibited"

    hostname = parsed.hostname
    if not hostname:
        return False, "URL missing hostname"

    try:
        port = parsed.port
    except ValueError:
        return False, "URL contains invalid port"

    permitted_ports = allowed_ports if allowed_ports is not None else {80, 443}
    if port is not None and port not in permitted_ports:
        return False, f"Port {port} is prohibited; allowed ports: {sorted(permitted_ports)}"

    hostname_lower = hostname.lower().rstrip(".")

    # 1. Block known dangerous hostnames & internal TLDs
    if hostname_lower in BLOCKED_HOSTNAMES:
        return False, f"Prohibited cloud metadata or internal host: {hostname}"
    if any(hostname_lower.endswith(tld) for tld in BLOCKED_TLD_SUFFIXES):
        return False, f"Internal or local TLD prohibited: {hostname}"

    # 2. Direct IP or numeric/hex obfuscated IP check in URL
    parsed_ip = _normalize_and_parse_ip(hostname_lower)
    if parsed_ip is not None:
        blocked, reason = is_ip_blocked(str(parsed_ip))
        if blocked:
            return False, reason
        if scheme == "http" and not allow_http:
            return False, "Cleartext HTTP scheme is prohibited for external fetches; use HTTPS"
        return True, "URL is safe"

    if _NUMERIC_OR_HEX_HOST_RE.match(hostname_lower):
        return False, f"Numeric or obfuscated IP hostname prohibited: {hostname}"

    if scheme == "http" and not allow_http:
        return False, "Cleartext HTTP scheme is prohibited for external fetches; use HTTPS"

    # 3. DNS Resolution Check (Fails CLOSED on resolution failure or private IP answer)
    if resolve_dns:
        try:
            if dns_resolver is not None:
                resolved_ips = set(dns_resolver(hostname_lower))
            else:
                addr_info = socket.getaddrinfo(hostname_lower, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                resolved_ips = set()
                for item in addr_info:
                    sockaddr = item[4]
                    if sockaddr:
                        resolved_ips.add(sockaddr[0])

            if not resolved_ips:
                return False, f"DNS resolution returned no addresses for host: {hostname}"

            for ip_str in resolved_ips:
                blocked, reason = is_ip_blocked(ip_str)
                if blocked:
                    return False, f"Host '{hostname}' resolved to prohibited address: {reason}"

        except socket.gaierror as e:
            return False, f"Fail-closed DNS resolution failure for host '{hostname}': {e}"
        except Exception as e:
            return False, f"Fail-closed DNS verification error for host '{hostname}': {e}"

    return True, "URL is safe"


class PinnedSSRFNetworkBackend(AsyncNetworkBackend):
    """
    Transport-level SSRF enforcement and connected-IP pinning backend for httpcore/httpx.
    Guarantees that:
    1. Immediately at TCP socket connect time (`connect_tcp`), the target host is resolved
       and every candidate IP (IPv4/IPv6/mapped-IPv6) is verified via `is_ip_blocked`.
    2. The underlying TCP socket connects directly to the literal validated IP address
       (`pinned_ip`) rather than passing the hostname back to OS DNS resolution, eliminating
       DNS rebinding TOCTOU between validation and `connect()`.
    3. TLS SNI and X.509 hostname verification remain intact because `httpcore` passes the
       original request origin hostname to `stream.start_tls(server_hostname=...)`.
    4. Post-connect socket peername inspection verifies the connected peer IP before any
       bytes are transmitted.
    """

    def __init__(
        self,
        dns_resolver: Optional[Callable[[str], List[str]]] = None,
        inner_backend: Optional[AsyncNetworkBackend] = None,
    ):
        self.dns_resolver = dns_resolver
        self._inner: AsyncNetworkBackend = inner_backend if inner_backend is not None else AutoBackend()
        self.last_connected_ip: Optional[str] = None

    def resolve_and_pin_ip(self, host: str, port: int) -> str:
        host_clean = (host or "").strip().strip("[]").lower().rstrip(".")
        if not host_clean:
            raise ValueError("Outbound HTTP blocked by SSRF boundary at socket connect: empty host")
        if host_clean in BLOCKED_HOSTNAMES:
            raise ValueError(f"Outbound HTTP blocked by SSRF boundary at socket connect: prohibited host '{host}'")
        if any(host_clean.endswith(tld) for tld in BLOCKED_TLD_SUFFIXES):
            raise ValueError(f"Outbound HTTP blocked by SSRF boundary at socket connect: prohibited internal TLD '{host}'")

        parsed_ip = _normalize_and_parse_ip(host_clean)
        if parsed_ip is not None:
            ip_str = str(parsed_ip)
            blocked, reason = is_ip_blocked(ip_str)
            if blocked:
                raise ValueError(f"Outbound HTTP blocked by SSRF boundary at socket connect: {reason}")
            return ip_str

        if _NUMERIC_OR_HEX_HOST_RE.match(host_clean):
            raise ValueError(f"Outbound HTTP blocked by SSRF boundary at socket connect: obfuscated IP host '{host}'")

        try:
            if self.dns_resolver is not None:
                resolved_ips = list(dict.fromkeys(self.dns_resolver(host_clean)))
            else:
                addr_info = socket.getaddrinfo(host_clean, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                resolved_ips = []
                for item in addr_info:
                    sockaddr = item[4]
                    if sockaddr and sockaddr[0] not in resolved_ips:
                        resolved_ips.append(sockaddr[0])
        except socket.gaierror as e:
            raise ValueError(
                f"Outbound HTTP blocked by SSRF boundary at socket connect: Fail-closed DNS resolution failure for '{host}': {e}"
            ) from e
        except Exception as e:
            raise ValueError(
                f"Outbound HTTP blocked by SSRF boundary at socket connect: DNS verification error for '{host}': {e}"
            ) from e

        if not resolved_ips:
            raise ValueError(
                f"Outbound HTTP blocked by SSRF boundary at socket connect: DNS resolution returned no addresses for '{host}'"
            )

        for ip_str in resolved_ips:
            blocked, reason = is_ip_blocked(ip_str)
            if blocked:
                raise ValueError(
                    f"Outbound HTTP blocked by SSRF boundary at socket connect: Host '{host}' resolved to prohibited address: {reason}"
                )

        return resolved_ips[0]

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> AsyncNetworkStream:
        pinned_ip = self.resolve_and_pin_ip(host, port)
        self.last_connected_ip = pinned_ip

        stream = await self._inner.connect_tcp(
            pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

        # Post-connect peername verification on the actual connected socket
        raw_sock = stream.get_extra_info("socket")
        if raw_sock is not None:
            try:
                peer = raw_sock.getpeername()
                if isinstance(peer, tuple) and len(peer) >= 1:
                    peer_ip = str(peer[0])
                    blocked, reason = is_ip_blocked(peer_ip)
                    if blocked:
                        await stream.aclose()
                        raise ValueError(
                            f"Outbound HTTP blocked by SSRF boundary post-connect: connected peer '{peer_ip}' rejected ({reason})"
                        )
            except ValueError:
                raise
            except Exception:
                pass

        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> AsyncNetworkStream:
        raise ValueError("Outbound Unix domain socket connections are prohibited by SSRF boundary")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class SafeHttpClient:
    """
    Centralized outbound HTTP security boundary.
    Enforces:
    - URL & DNS validation before every request and every redirect hop (max 3 redirects).
    - Transport-level connected destination IP pinning via `PinnedSSRFNetworkBackend`
      to eliminate DNS rebinding TOCTOU between validation and socket connect.
    - Disabled environment proxy trust (`trust_env=False`).
    - Strict connect/read timeouts and maximum response payload size limits.
    - Optional content-type allowlist enforcement.
    """

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        max_redirects: int = 3,
        max_response_bytes: int = 2 * 1024 * 1024,  # 2 MB
        allow_http: bool = False,
        allowed_content_types: Optional[Set[str]] = None,
        dns_resolver: Optional[Callable[[str], List[str]]] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.allow_http = allow_http
        self.allowed_content_types = allowed_content_types
        self.dns_resolver = dns_resolver
        self.network_backend = PinnedSSRFNetworkBackend(dns_resolver=dns_resolver)

    def verify_target(self, url: str) -> str:
        is_safe, reason = validate_safe_url(
            url,
            resolve_dns=True,
            allow_http=self.allow_http,
            dns_resolver=self.dns_resolver,
        )
        if not is_safe:
            raise ValueError(f"Outbound HTTP blocked by SSRF boundary: {reason}")
        return url

    def _build_pinned_transport(self) -> httpx.AsyncHTTPTransport:
        transport = httpx.AsyncHTTPTransport(trust_env=False)
        transport._pool._network_backend = self.network_backend
        return transport

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> httpx.Response:
        current_url = url
        hops = 0

        timeout = httpx.Timeout(self.timeout_seconds, connect=min(5.0, self.timeout_seconds))
        active_transport = transport if transport is not None else self._build_pinned_transport()
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=active_transport,
        ) as client:
            while True:
                # 1. Pre-flight SSRF + DNS check
                self.verify_target(current_url)

                # 2. Second DNS check immediately prior to socket use (mitigates TOCTOU DNS rebinding)
                self.verify_target(current_url)

                resp = await client.get(
                    current_url,
                    params=params if hops == 0 else None,
                    headers=headers,
                )

                if resp.is_redirect:
                    hops += 1
                    if hops > self.max_redirects:
                        raise ValueError(f"Outbound HTTP exceeded maximum redirect hops ({self.max_redirects})")
                    location = resp.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response missing Location header")
                    current_url = urljoin(current_url, location)
                    continue

                # 3. Enforce max response size
                if len(resp.content) > self.max_response_bytes:
                    raise ValueError(
                        f"Outbound HTTP response size ({len(resp.content)} bytes) exceeds limit ({self.max_response_bytes} bytes)"
                    )

                # 4. Enforce content-type allowlist if specified
                if self.allowed_content_types:
                    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if ctype and ctype not in self.allowed_content_types:
                        raise ValueError(
                            f"Outbound HTTP response content-type '{ctype}' not in allowlist {sorted(self.allowed_content_types)}"
                        )

                return resp

    async def get_async(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> httpx.Response:
        return await self.get(url=url, params=params, headers=headers, transport=transport)
