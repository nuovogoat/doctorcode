"""mDNS advertisement so the Android companion can find this desktop.

Discovery only publishes the endpoint, version, and whether pairing is needed.
It never publishes pairing codes, bearer tokens, API keys, or project paths.
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import socket
from typing import Any

from zeroconf import ServiceInfo, Zeroconf

SERVICE_TYPE = "_doctorcode._tcp.local."
SERVICE_LABEL = "DoctorCode"
PATH = "/api"
PROPERTY_VERSION = b"0.1.0"


def _clean_hostname() -> str:
    raw = socket.gethostname() or "doctorcode"
    cleaned = "".join(char for char in raw if char.isalnum() or char == "-").strip("-")
    return (cleaned or "doctorcode")[:40]


def local_addresses() -> list[str]:
    """Return usable IPv4 addresses without sending traffic to the internet."""
    found: set[str] = set()
    try:
        for family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        ):
            if family != socket.AF_INET:
                continue
            address = str(sockaddr[0])
            if ipaddress.ip_address(address).is_loopback:
                continue
            found.add(address)
    except OSError:
        pass
    if not found:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # TEST-NET-1 address; connect() does not send packets.
            probe.connect(("192.0.2.1", 9))
            address = str(probe.getsockname()[0])
            if not ipaddress.ip_address(address).is_loopback:
                found.add(address)
        except OSError:
            pass
        finally:
            probe.close()
    return sorted(found)


class ServiceAdvertiser:
    """Best-effort mDNS publisher; failure never prevents LAN access."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self._zeroconf: Zeroconf | None = None
        self._info: ServiceInfo | None = None
        self._addresses: list[str] = []
        self._port = 0

    @property
    def running(self) -> bool:
        return self._zeroconf is not None

    @property
    def service_name(self) -> str:
        return f"{SERVICE_LABEL} ({_clean_hostname()})"

    @property
    def addresses(self) -> list[str]:
        return list(self._addresses)

    @property
    def port(self) -> int:
        return self._port

    def start(self, port: int, *, pairing_required: bool) -> bool:
        if self.running:
            return True
        addresses = local_addresses()
        if not addresses:
            self.logger.info("server.mdns_skipped: no LAN IPv4 address")
            return False
        self._addresses = addresses
        self._port = port
        instance = f"{self.service_name}.{SERVICE_TYPE}"
        zeroconf: Zeroconf | None = None
        try:
            info = ServiceInfo(
                SERVICE_TYPE,
                instance,
                addresses=[socket.inet_aton(address) for address in addresses],
                port=port,
                properties={
                    "version": PROPERTY_VERSION.decode("ascii"),
                    "pairing": "required" if pairing_required else "disabled",
                    "path": PATH,
                },
                server=f"{_clean_hostname()}.local.",
            )
            zeroconf = Zeroconf()
            zeroconf.register_service(info)
        except Exception:
            self.logger.exception("server.mdns_start_failed")
            if zeroconf is not None:
                with contextlib.suppress(Exception):
                    zeroconf.close()
            self._zeroconf = None
            self._info = None
            self._addresses = []
            self._port = 0
            return False
        self._zeroconf = zeroconf
        self._info = info
        return True

    def stop(self) -> None:
        zeroconf, info = self._zeroconf, self._info
        self._zeroconf = None
        self._info = None
        if zeroconf is None:
            return
        try:
            if info is not None:
                zeroconf.unregister_service(info)
        except Exception:
            self.logger.exception("server.mdns_unregister_failed")
        finally:
            try:
                zeroconf.close()
            except Exception:
                self.logger.exception("server.mdns_close_failed")

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "service_type": SERVICE_TYPE,
            "service_name": self.service_name,
            "addresses": self.addresses,
            "port": self.port,
        }
