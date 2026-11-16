#!/usr/bin/env python3
"""Robust, transport-agnostic LA66 LoRaWAN driver for the UNO Q.

A consolidation of the field-proven ``lora.py`` (raw ``:7500`` socket
driver — reconnect, background reader, duty-cycle pacing, crash-durable
spool) with the two things it lacked for a clean multi-network interface:

  1. a **Link seam** so the same driver runs over the arduino-router
     ``:7500`` socket, a direct USB serial (Dragino LA66 USB adapter), or
     an in-memory fake for hardware-free tests; and
  2. **provisioning + OTAA join** (`AT+NJM`/`AT+JOIN`/`AT+NJS`, key setters)
     so a device can be pointed at LORIOT, TTN, or a custom network server
     without hand-copying factory ABP keys.

Everything the network server sees is standard LoRaWAN, so LORIOT / TTN /
ChirpStack / a custom packet-forwarder differ only in *device config*
(activation + keys), which this module surfaces — not in code.

Stdlib-only for the socket path (the board has no pip); pyserial is imported
lazily and only if you use ``SerialLink``.

Quick use
---------
    from lorawan import LA66, SocketLink, build_beacon

    la = LA66(SocketLink())                 # framework-free :7500 path
    la.connect()
    la.provision_otaa(dev_eui="A840...", app_eui="0000...", app_key="2B7E...")
    la.join()                               # AT+JOIN, waits for AT+NJS=1
    la.configure(dr=5, adr=True)            # full plan + ADR (deployment posture)
    res = la.send(build_beacon(tick=42))    # blocks until txDone; duty-cycle paced
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import socket
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("lorawan")


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class LoRaError(Exception):
    """Base class for all LA66 driver errors."""


class LoRaConnectionError(LoRaError):
    """Could not reach / lost the underlying link (socket, serial, …)."""


class LoRaTimeout(LoRaError):
    """The module did not produce the expected reply in time."""


class LoRaModuleError(LoRaError):
    """The LA66 answered AT_ERROR / AT_BUSY / AT_PARAM_ERROR etc."""


class LoRaDutyCycle(LoRaError):
    """Send refused because the EU868 duty-cycle budget is not yet available."""


class LoRaJoinError(LoRaError):
    """An OTAA join did not complete (AT+NJS never reached 1)."""


# --------------------------------------------------------------------------- #
# Link seam — the one abstraction that makes the driver transport-agnostic
# --------------------------------------------------------------------------- #
class Link(ABC):
    """A raw byte pipe to the LA66's AT interface.

    The driver owns all AT/line semantics; a Link only moves bytes. Three
    implementations ship: SocketLink (arduino-router :7500), SerialLink
    (direct UART / USB adapter), and FakeLink (tests).
    """

    @abstractmethod
    def open(self) -> None:
        """Establish (or re-establish) the connection. Raise
        LoRaConnectionError on failure."""

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Write raw bytes. Raise LoRaConnectionError if the link is gone."""

    @abstractmethod
    def recv(self, timeout: float) -> bytes:
        """Return up to some bytes, or b"" if none arrived within timeout.
        Never blocks longer than ``timeout``."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection (idempotent)."""

    def describe(self) -> str:
        """Short human label for logs."""
        return type(self).__name__


class SocketLink(Link):
    """The field-proven path: arduino-router's transparent tunnel on
    127.0.0.1:7500, bridged to the STM32's Serial→LA66."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7500,
                 connect_timeout: float = 5.0):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self._sock: Optional[socket.socket] = None

    def open(self) -> None:
        self.close()
        try:
            s = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout)
        except OSError as e:
            raise LoRaConnectionError(
                f"cannot reach {self.host}:{self.port}: {e}") from e
        s.settimeout(1.0)
        self._sock = s

    def send(self, data: bytes) -> None:
        if self._sock is None:
            raise LoRaConnectionError("socket not open")
        try:
            self._sock.sendall(data)
        except OSError as e:
            raise LoRaConnectionError(f"write failed: {e}") from e

    def recv(self, timeout: float) -> bytes:
        if self._sock is None:
            raise LoRaConnectionError("socket not open")
        self._sock.settimeout(timeout)
        try:
            return self._sock.recv(4096)
        except socket.timeout:
            return b""
        except OSError as e:
            raise LoRaConnectionError(f"read failed: {e}") from e

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def describe(self) -> str:
        return f"SocketLink({self.host}:{self.port})"


class SerialLink(Link):
    """Direct UART: the Dragino LA66 *USB adapter* (/dev/ttyUSB0) or any board
    where Linux owns the LA66's serial line. pyserial imported lazily."""

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self._ser = None

    def open(self) -> None:
        self.close()
        try:
            import serial
        except ImportError as e:  # pragma: no cover - env-dependent
            raise LoRaConnectionError(
                "pyserial not installed (needed for SerialLink)") from e
        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0.2)
        except Exception as e:
            raise LoRaConnectionError(
                f"cannot open {self.port}: {e}") from e

    def send(self, data: bytes) -> None:
        if self._ser is None:
            raise LoRaConnectionError("serial not open")
        try:
            self._ser.write(data)
        except Exception as e:
            raise LoRaConnectionError(f"write failed: {e}") from e

    def recv(self, timeout: float) -> bytes:
        if self._ser is None:
            raise LoRaConnectionError("serial not open")
        self._ser.timeout = timeout
        try:
            return self._ser.read(max(1, self._ser.in_waiting or 1))
        except Exception as e:
            raise LoRaConnectionError(f"read failed: {e}") from e

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def describe(self) -> str:
        return f"SerialLink({self.port}@{self.baudrate})"


# --------------------------------------------------------------------------- #
# LoRa airtime (Semtech formula) — used to pace the EU868 1% duty cycle
# --------------------------------------------------------------------------- #
DR_TO_SF = {0: 12, 1: 11, 2: 10, 3: 9, 4: 8, 5: 7}
LORAWAN_OVERHEAD_BYTES = 13  # MHDR+FHDR+FPort+MIC over the app payload


def lora_airtime_s(app_payload_len: int, sf: int = 12, bw_hz: int = 125_000,
                   coding_rate: int = 1, preamble: int = 8,
                   explicit_header: bool = True, crc: bool = True) -> float:
    """Time-on-air (s) for a LoRaWAN frame carrying ``app_payload_len`` bytes.
    Conservative (defaults to SF12)."""
    phy_len = app_payload_len + LORAWAN_OVERHEAD_BYTES
    t_sym = (2 ** sf) / bw_hz
    de = 1 if (bw_hz == 125_000 and sf >= 11) else 0
    ih = 0 if explicit_header else 1
    crc_on = 1 if crc else 0
    numerator = 8 * phy_len - 4 * sf + 28 + 16 * crc_on - 20 * ih
    denominator = 4 * (sf - 2 * de)
    payload_symbols = 8 + max(
        math.ceil(numerator / denominator) * (coding_rate + 4), 0)
    return (preamble + 4.25) * t_sym + payload_symbols * t_sym


# --------------------------------------------------------------------------- #
# Payload helper (the tagged 7-byte beacon the ingest expects)
# --------------------------------------------------------------------------- #
BEACON_MAGIC = 0xAC
BEACON_TAG = ord("Q")  # 0x51


def build_beacon(tick: int, site: int = 0, tag: int = BEACON_TAG,
                 magic: int = BEACON_MAGIC) -> bytes:
    """magic(1) tag(1) tick(u32 LE) site(1) → 7 bytes."""
    if not (0 <= tick <= 0xFFFFFFFF):
        raise ValueError("tick must fit in uint32")
    return (bytes([magic & 0xFF, tag & 0xFF])
            + int(tick).to_bytes(4, "little") + bytes([site & 0xFF]))


@dataclass
class SendResult:
    """Outcome of one uplink."""

    ok: bool
    tx_done: bool
    fcnt: Optional[int]
    freq_mhz: Optional[str]
    dr: Optional[int]
    airtime_s: float
    elapsed_s: float
    raw: str = field(repr=False, default="")

    def __str__(self) -> str:
        return (f"SendResult(ok={self.ok} fcnt={self.fcnt} "
                f"freq={self.freq_mhz} dr={self.dr} "
                f"airtime={self.airtime_s:.2f}s elapsed={self.elapsed_s:.2f}s)")


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #
_RE_TX = re.compile(r"TX on freq\s+([\d.]+)\s*MHz\s+at DR\s+(\d+)", re.I)
_RE_FCNT = re.compile(r"UpLinkCounter=\s*(\d+)", re.I)
_ERROR_TOKENS = ("AT_ERROR", "AT_BUSY", "AT_PARAM_ERROR",
                 "AT_NO_NETWORK_JOINED", "ERROR(", "+CME ERROR")


class LA66:
    """Thread-safe, reconnecting, duty-cycle-aware LA66 driver over any Link.

    Every AT transaction is serialized by an internal lock, so concurrent
    callers queue rather than corrupt each other. Prefer one process-wide
    instance (see get_link()).
    """

    def __init__(self, link: Optional[Link] = None, *, assume_sf: int = 12,
                 duty_cycle: float = 0.01, respect_duty_cycle: bool = True,
                 duty_cycle_block: bool = True, reconnect_backoff: float = 2.0,
                 max_reconnect_backoff: float = 30.0, warm_up: bool = True):
        self.link: Link = link if link is not None else SocketLink()
        self.sf = assume_sf
        self.duty_cycle = duty_cycle
        self.respect_duty_cycle = respect_duty_cycle
        self.duty_cycle_block = duty_cycle_block
        self.reconnect_backoff = reconnect_backoff
        self.max_reconnect_backoff = max_reconnect_backoff
        self.warm_up = warm_up

        self._rx: "queue.Queue[str]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()
        self._lock = threading.RLock()
        self._next_ok = 0.0
        self._closed = False

    # ---- lifecycle ------------------------------------------------------- #
    def connect(self) -> None:
        with self._lock:
            self._open_link()
            if self.warm_up:
                self._do_warm_up()

    def _open_link(self) -> None:
        self._teardown_link()
        self.link.open()
        self._reader_stop.clear()
        self._rx = queue.Queue()  # fresh: no stale lines across a reconnect
        self._reader = threading.Thread(
            target=self._read_loop, name="la66-rx", daemon=True)
        self._reader.start()
        log.info("connected via %s", self.link.describe())

    def _teardown_link(self) -> None:
        self._reader_stop.set()
        self.link.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._teardown_link()

    def __enter__(self) -> "LA66":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- background reader ---------------------------------------------- #
    def _read_loop(self) -> None:
        buf = b""
        while not self._reader_stop.is_set():
            try:
                chunk = self.link.recv(1.0)
            except LoRaConnectionError:
                break
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "ignore").strip("\r").rstrip()
                if text:
                    self._rx.put(text)
        log.debug("reader thread exiting")

    # ---- low-level io --------------------------------------------------- #
    def _ensure_connected(self) -> None:
        if self._closed:
            raise LoRaConnectionError("driver is closed")
        if self._reader is None or not self._reader.is_alive():
            self._reconnect()

    def _reconnect(self) -> None:
        backoff = self.reconnect_backoff
        while not self._closed:
            try:
                self._open_link()
                if self.warm_up:
                    self._do_warm_up()
                return
            except LoRaConnectionError as e:
                log.warning("reconnect failed (%s); retry in %.0fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, self.max_reconnect_backoff)
        raise LoRaConnectionError("driver closed during reconnect")

    def _drain(self) -> None:
        try:
            while True:
                self._rx.get_nowait()
        except queue.Empty:
            pass

    def _do_warm_up(self) -> None:
        """Absorb boot bytes and the 'first AT after boot returns nothing'
        quirk by firing a throwaway AT and draining."""
        try:
            time.sleep(0.2)
            self._drain()
            self.link.send(b"AT\r\n")
            time.sleep(0.4)
            self._drain()
        except LoRaError:
            pass  # best-effort

    # ---- AT transactions ------------------------------------------------ #
    def at(self, cmd: str, *, terminators=("OK",), timeout: float = 4.0,
           error_tokens=_ERROR_TOKENS) -> list[str]:
        """Send one AT command, collect reply lines up to a terminator.
        Raises LoRaModuleError / LoRaTimeout / LoRaConnectionError. Thread-safe.
        """
        with self._lock:
            self._ensure_connected()
            self._drain()
            data = (cmd + "\r\n").encode("ascii", "ignore")
            try:
                self.link.send(data)
            except LoRaConnectionError:
                self._reconnect()
                self.link.send(data)

            deadline = time.monotonic() + timeout
            collected: list[str] = []
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LoRaTimeout(
                        f"{cmd!r}: no terminator in {timeout:.1f}s; "
                        f"got {collected!r}")
                try:
                    line = self._rx.get(timeout=remaining)
                except queue.Empty:
                    continue
                collected.append(line)
                up = line.upper()
                if any(tok in up for tok in error_tokens):
                    raise LoRaModuleError(f"{cmd!r} -> {line!r}")
                if any(term.upper() in up for term in terminators):
                    return collected

    # ---- provisioning + activation -------------------------------------- #
    def provision_otaa(self, dev_eui: str, app_key: str,
                       app_eui: str = "0000000000000000") -> None:
        """Set OTAA credentials (hex strings). Call join() afterwards.

        app_eui (a.k.a. JoinEUI) is often all-zeros on TTN/LORIOT — the
        default reflects that. dev_eui is 16 hex chars, app_key 32.
        """
        self.at("AT+NJM=1")                       # OTAA
        self.at(f"AT+DEUI={_hex(dev_eui, 16)}")
        self.at(f"AT+APPEUI={_hex(app_eui, 16)}")
        self.at(f"AT+APPKEY={_hex(app_key, 32)}")

    def provision_abp(self, dev_addr: str, nwkskey: str, appskey: str) -> None:
        """Set ABP credentials (hex strings): dev_addr 8 hex chars, keys 32."""
        self.at("AT+NJM=0")                       # ABP
        self.at(f"AT+DADDR={_hex(dev_addr, 8)}")
        self.at(f"AT+NWKSKEY={_hex(nwkskey, 32)}")
        self.at(f"AT+APPSKEY={_hex(appskey, 32)}")

    def joined(self) -> bool:
        """True if the module reports network-joined (AT+NJS=1). ABP devices
        are always 'joined'."""
        for ln in self.at("AT+NJS=?", timeout=4.0):
            m = re.search(r"\b([01])\b", ln)
            if m:
                return m.group(1) == "1"
        return False

    def join(self, *, timeout: float = 120.0, poll: float = 3.0,
             retries: int = 2) -> None:
        """Run an OTAA join and wait until AT+NJS reports joined.

        Verifies success via AT+NJS rather than a firmware-specific 'JOINED'
        string, so it is robust across LA66 firmware wordings.

        Raises
        ------
        LoRaJoinError
            If the module is not joined within ``timeout`` after ``retries``
            AT+JOIN attempts.
        """
        if self.joined():
            return
        for attempt in range(1, retries + 1):
            log.info("OTAA join attempt %d/%d", attempt, retries)
            try:
                self.at("AT+JOIN", terminators=("OK", "JOIN"), timeout=8.0)
            except LoRaModuleError as e:
                log.warning("AT+JOIN reported %s; polling NJS anyway", e)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.joined():
                    log.info("joined the network")
                    return
                time.sleep(poll)
        raise LoRaJoinError(
            f"not joined after {retries} attempts / {timeout:.0f}s each")

    # ---- radio config --------------------------------------------------- #
    def configure(self, *, dr: Optional[int] = None, adr: Optional[bool] = None,
                  full_channel_plan: bool = False,
                  single_channel_hz: Optional[int] = None) -> None:
        """Set the radio posture.

        Deployment default is the full channel plan with ADR on (let the
        network optimize DR). Single-channel (home test gateway) forces ADR
        off + a fixed DR. A channel-plan change issues ATZ, because AT+CHS
        only takes effect after a module reset — the one real bug in the
        original driver.

        Parameters
        ----------
        dr : int, optional
            Data rate 0..5 (0=SF12 max range, 5=SF7). Also updates the airtime
            model used for duty-cycle pacing.
        adr : bool, optional
            Adaptive Data Rate. Defaults: True when full_channel_plan, forced
            False when single_channel_hz.
        full_channel_plan : bool
            AT+CHS=0 — use the whole EU868 plan (real multi-channel gateway).
        single_channel_hz : int, optional
            Pin one frequency (single-channel test gateway), e.g. 868100000.
        """
        chs_changed = False
        if single_channel_hz is not None:
            if adr is None:
                adr = False  # a fixed channel needs ADR off
            self.at(f"AT+CHS={int(single_channel_hz)}")
            chs_changed = True
        elif full_channel_plan:
            if adr is None:
                adr = True
            self.at("AT+CHS=0")
            chs_changed = True

        if adr is not None:
            self.at(f"AT+ADR={1 if adr else 0}")
        if dr is not None:
            self.at(f"AT+DR={int(dr)}")
            self.sf = DR_TO_SF.get(int(dr), self.sf)

        if chs_changed:
            self._reset_module()

    def _reset_module(self) -> None:
        """ATZ so a channel-plan change takes effect, then re-warm-up."""
        try:
            self.at("ATZ", terminators=("OK", "DRAGINO", "LA66"), timeout=6.0)
        except LoRaError:
            pass  # ATZ often returns boot banner rather than OK
        if self.warm_up:
            self._do_warm_up()

    def cfg(self) -> str:
        """Raw AT+CFG dump (DevEUI/DevAddr/keys/band)."""
        return "\n".join(self.at("AT+CFG", terminators=("OK",), timeout=5.0))

    # ---- duty cycle ----------------------------------------------------- #
    def _pace(self, airtime_s: float) -> None:
        if not self.respect_duty_cycle:
            return
        now = time.monotonic()
        if now < self._next_ok:
            wait = self._next_ok - now
            if self.duty_cycle_block:
                log.info("duty-cycle: waiting %.1fs", wait)
                time.sleep(wait)
            else:
                raise LoRaDutyCycle(f"duty cycle: retry in {wait:.1f}s")

    def _register_tx(self, airtime_s: float) -> None:
        off = airtime_s * (1.0 / self.duty_cycle - 1.0)
        self._next_ok = time.monotonic() + off

    def time_until_ready(self) -> float:
        """Seconds until the duty cycle permits another uplink (0 if ready)."""
        return max(0.0, self._next_ok - time.monotonic())

    # ---- send ----------------------------------------------------------- #
    def send(self, payload: bytes, *, fport: int = 2, confirm: bool = False,
             timeout: Optional[float] = None, retries: int = 1) -> SendResult:
        """Transmit ``payload`` as a LoRaWAN uplink and wait for txDone.

        ``confirm=True`` requests a network ACK (needs a multi-channel
        gateway that can send the downlink); ``confirm=False`` is fire-and-
        forget (only txDone). Blocks for duty-cycle pacing unless
        duty_cycle_block=False (then raises LoRaDutyCycle).
        """
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload must be bytes")
        n = len(payload)
        if not (1 <= n <= 242):
            raise ValueError("payload length out of range")
        hexs = bytes(payload).hex().upper()
        airtime = lora_airtime_s(n, sf=self.sf)
        cmd = f"AT+SENDB={1 if confirm else 0},{fport},{n},{hexs}"
        # SF12 rx1/rx2 windows add ~6 s after txDone; be generous so a
        # confirmed uplink's downlink window isn't cut off.
        eff_timeout = timeout if timeout is not None else max(
            8.0, airtime * 2 + 8.0)

        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= retries:
            attempt += 1
            self._pace(airtime)
            t0 = time.monotonic()
            try:
                lines = self.at(cmd, terminators=("TXDONE",),
                                timeout=eff_timeout)
            except (LoRaConnectionError, LoRaModuleError) as e:
                last_exc = e
                transient = (isinstance(e, LoRaConnectionError)
                             or "AT_BUSY" in str(e).upper())
                if transient and attempt <= retries:
                    log.warning("send attempt %d failed (%s); retrying",
                                attempt, e)
                    time.sleep(1.5 * attempt)
                    continue
                raise
            elapsed = time.monotonic() - t0
            self._register_tx(airtime)
            raw = "\n".join(lines)
            freq = dr = fcnt = None
            m = _RE_TX.search(raw)
            if m:
                freq, dr = m.group(1), int(m.group(2))
            m = _RE_FCNT.search(raw)
            if m:
                fcnt = int(m.group(1))
            result = SendResult(True, True, fcnt, freq, dr, airtime, elapsed,
                                raw)
            log.info("sent %s", result)
            return result
        assert last_exc is not None
        raise last_exc

    def send_beacon(self, tick: int, site: int = 0, **kw) -> SendResult:
        """Convenience: build_beacon(tick, site) then send()."""
        return self.send(build_beacon(tick, site), **kw)


def _hex(s: str, want_chars: int) -> str:
    """Validate + normalize a hex credential string to upper-case."""
    clean = s.replace(":", "").replace(" ", "").strip()
    if len(clean) != want_chars or not re.fullmatch(r"[0-9a-fA-F]+", clean):
        raise ValueError(f"expected {want_chars} hex chars, got {s!r}")
    return clean.upper()


# --------------------------------------------------------------------------- #
# Process-wide singleton + module-level convenience
# --------------------------------------------------------------------------- #
_default_link: Optional[LA66] = None
_default_lock = threading.Lock()


def get_link(link: Optional[Link] = None, **kw) -> LA66:
    """Return the shared, lazily-connected LA66 instance for this process."""
    global _default_link
    with _default_lock:
        if _default_link is None:
            _default_link = LA66(link, **kw)
            _default_link.connect()
        return _default_link


def send(payload: bytes, **kw) -> SendResult:
    """Convenience: send ``payload`` on the shared link."""
    return get_link().send(payload, **kw)


# --------------------------------------------------------------------------- #
# Durable background sender — the shape the inference pipeline should use
# --------------------------------------------------------------------------- #
class DiskSpoolSender(threading.Thread):
    """Crash-durable, non-blocking uplink queue.

    Producers call enqueue(payload) and return immediately; the payload is
    written to a spool file first, so it survives a process crash. One worker
    drains the spool oldest-first, respecting the duty cycle, and deletes each
    file only after a confirmed txDone. Permanently-failing messages are moved
    aside to ``<name>.failed``.
    """

    def __init__(self, link: LA66, spool_dir: str, *, poll_interval: float = 1.0,
                 max_attempts: int = 5):
        super().__init__(name="la66-spool", daemon=True)
        self.link = link
        self.spool_dir = spool_dir
        self.poll_interval = poll_interval
        self.max_attempts = max_attempts
        self._stop = threading.Event()
        os.makedirs(spool_dir, exist_ok=True)

    def enqueue(self, payload: bytes, *, fport: int = 2,
                confirm: bool = False) -> str:
        """Persist a message for sending. Returns the spool file path."""
        rec = {"hex": bytes(payload).hex(), "fport": fport,
               "confirm": confirm, "attempts": 0}
        name = f"{time.time_ns():020d}-{os.getpid()}.msg"
        path = os.path.join(self.spool_dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, path)  # atomic publish
        return path

    def stop(self) -> None:
        self._stop.set()

    def _pending(self) -> list[str]:
        return sorted(
            os.path.join(self.spool_dir, n)
            for n in os.listdir(self.spool_dir) if n.endswith(".msg"))

    def run(self) -> None:
        log.info("spool sender started on %s", self.spool_dir)
        while not self._stop.is_set():
            paths = self._pending()
            if not paths:
                self._stop.wait(self.poll_interval)
                continue
            for path in paths:
                if self._stop.is_set():
                    break
                wait = self.link.time_until_ready()
                if wait > 0:
                    self._stop.wait(min(wait, 5.0))
                    break
                self._process(path)
        log.info("spool sender stopped")

    def _process(self, path: str) -> None:
        try:
            with open(path) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            self._fail(path, "unreadable")
            return
        payload = bytes.fromhex(rec["hex"])
        try:
            self.link.send(payload, fport=rec.get("fport", 2),
                           confirm=rec.get("confirm", False))
            os.remove(path)  # confirmed txDone -> done
        except LoRaDutyCycle:
            return  # try again next loop
        except LoRaError as e:
            rec["attempts"] = rec.get("attempts", 0) + 1
            log.warning("send failed (%s) attempt %d/%d for %s",
                        e, rec["attempts"], self.max_attempts,
                        os.path.basename(path))
            if rec["attempts"] >= self.max_attempts:
                self._fail(path, str(e))
            else:
                with open(path, "w") as f:
                    json.dump(rec, f)
                time.sleep(1.0)

    def _fail(self, path: str, why: str) -> None:
        try:
            os.replace(path, path + ".failed")
            log.error("giving up on %s (%s)", os.path.basename(path), why)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# CLI: python3 lorawan.py [tick]   send one test beacon and report
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    tick = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    with LA66(SocketLink()) as la:
        print("--- AT+CFG ---")
        try:
            print(la.cfg())
        except LoRaError as e:
            print(f"(cfg failed: {e})")
        print(f"--- sending beacon tick={tick} ---")
        print(la.send_beacon(tick))
