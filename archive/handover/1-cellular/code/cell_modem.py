#!/usr/bin/env python3
"""cell_modem.py — robust SIMCom A7670E (Cat-1 LTE) cellular MQTT driver.

The **cellular interface layer** for the UNO Q, analogous to `lora.py` on the
LoRa side. It wraps the modem's AT command set (SIMCom `CMQTT*` stack) behind a
small, defensive API so application code (`cell_bird_sender.py`, a heartbeat, a
real acoupi messenger, …) never touches raw serial or AT strings.

Why the modem's built-in MQTT and not PPP + paho? This board's stripped Debian
kernel has no `ppp_generic`, so `ppp0` can't come up (see CellularSetup.md). The
modem firmware's own MQTT stack, driven over its AT serial port, is the path that
works.

Design mirrors lora.py: one class, defensive primitives, typed exceptions, a
context manager, stdlib + pyserial only. Usage:

    import cell_modem
    with cell_modem.CellModem(apn="key") as m:            # opens + brings up network
        m.mqtt_connect("mqtt.cetools.org", 1884, "clientid", user="student", password="…")
        m.mqtt_publish("student/yeo/lora/yeo-unoq-4/up", '{"x":1}', qos=1)
        m.mqtt_disconnect()

Or drive the steps yourself (what cell_bird_sender does, so it can publish a whole
spool inside one connect/disconnect window):

    m = cell_modem.CellModem(apn="…"); m.open(); m.bring_up()
    m.mqtt_connect(...); for rec in spool: m.mqtt_publish(...); m.mqtt_disconnect()
    m.radio_off(); m.close()

THE OPERATIONAL GOTCHA THIS DRIVER GUARDS AGAINST: on Debian, **ModemManager**
grabs the modem's ttyUSB nodes and issues its own AT/connection commands, which
corrupts raw-AT use (intermittent `SIM failure`, `+CEREG: 3`, `CGACT` errors,
"multiple access on port"). Mask it: `sudo systemctl mask --now ModemManager`.
This driver detects when MM is the likely culprit and says so.
"""
from __future__ import annotations

import glob
import logging
import os
import time

log = logging.getLogger("cellmodem")


# --------------------------------------------------------------------------- #
# Exceptions — a small hierarchy so callers can react precisely.
# --------------------------------------------------------------------------- #
class ModemError(Exception):
    """Base for every modem/AT failure."""


class ModemNotPresent(ModemError):
    """No usable serial port: absent, unopenable, or pyserial missing.

    Callers treat this as 'skip and retry later', never fatal — the modem may
    simply not be attached yet."""


class ModemBusy(ModemNotPresent):
    """The port exists but is held by another process (very likely ModemManager)."""


class SimError(ModemError):
    """SIM not usable: not READY, PIN-locked, or 'SIM failure'."""


class RegistrationError(ModemError):
    """Not registered on the network (searching, denied, or no signal)."""


class MqttError(ModemError):
    """The modem's CMQTT stack reported a failure."""


# --------------------------------------------------------------------------- #
# Helper: is ModemManager the thing stealing our port?
# --------------------------------------------------------------------------- #
def modemmanager_active() -> bool:
    """Best-effort check for a running ModemManager (the classic port-contention
    cause). Tries `systemctl is-active`, falls back to scanning /proc."""
    try:
        import subprocess
        r = subprocess.run(["systemctl", "is-active", "ModemManager"],
                           capture_output=True, text=True, timeout=3)
        if r.stdout.strip() == "active":
            return True
    except Exception:
        pass
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                if open(f"/proc/{pid}/comm").read().strip() == "ModemManager":
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def _mm_hint(base: str) -> str:
    """Append a ModemManager remediation hint to an error message when relevant."""
    if modemmanager_active():
        return (base + " — ModemManager is ACTIVE and is probably holding the modem; "
                "mask it: `sudo systemctl mask --now ModemManager`")
    return base


# --------------------------------------------------------------------------- #
# The driver.
# --------------------------------------------------------------------------- #
class CellModem:
    """Defensive wrapper over the A7670E AT interface and its CMQTT stack.

    One instance == one serial connection. Not thread-safe; drive it from a
    single worker (the spool flusher), like lora.py's link."""

    #: candidate device globs, in the order we probe them for the AT port
    PORT_GLOBS = ("/dev/ttyUSB*", "/dev/ttyACM*")

    def __init__(self, port: str = "auto", baud: int = 115200, *,
                 # "key" = KeySIM/Tele2 (current SIM). Giffgaff/O2 was
                 # "giffgaff.com". Must match the SIM or CEREG returns 0,3.
                 apn: str = "key", lte_only: bool = True,
                 min_csq: int = 2):
        self.port = port                 # "auto" -> autodetect the AT port
        self.baud = baud
        self.apn = apn
        self.lte_only = lte_only
        self.min_csq = min_csq           # reject CSQ below this (99 == no signal)
        self.ser = None
        # CMQTT session state — so teardown only undoes what actually came up.
        self._started = False            # CMQTTSTART done
        self._acquired = False           # CMQTTACCQ done (client 0 exists)
        self._connected = False          # CMQTTCONNECT succeeded

    # ---- lifecycle -------------------------------------------------------- #
    @classmethod
    def _candidates(cls) -> list[str]:
        out: list[str] = []
        for g in cls.PORT_GLOBS:
            out.extend(sorted(glob.glob(g)))
        return out

    @staticmethod
    def _open_serial(port: str, baud: int, timeout: float):
        """Open exclusively (TIOCEXCL) so a second opener/ModemManager can't
        silently share the port. Falls back if the platform lacks exclusive."""
        import serial
        try:
            return serial.Serial(port, baud, timeout=timeout, exclusive=True)
        except TypeError:                # very old pyserial without `exclusive`
            return serial.Serial(port, baud, timeout=timeout)

    @classmethod
    def autodetect(cls, baud: int) -> str:
        """Return the first node that answers AT->OK. Raises ModemNotPresent /
        ModemBusy (with a ModemManager hint) when none is usable."""
        try:
            import serial  # noqa: F401
        except ImportError as e:
            raise ModemNotPresent(f"pyserial not installed ({e}); `pip install pyserial`")
        cands = cls._candidates()
        if not cands:
            raise ModemNotPresent("no /dev/ttyUSB*/ttyACM* nodes (modem attached?)")
        busy = 0
        for port in cands:
            try:
                s = cls._open_serial(port, baud, 0.3)
            except Exception:
                busy += 1                # held by someone else, or transient
                continue
            try:
                s.reset_input_buffer()
                s.write(b"AT\r")
                time.sleep(0.3)
                if "OK" in s.read(256).decode(errors="ignore"):
                    log.info("modem AT port auto-detected: %s", port)
                    return port
            except Exception:
                continue
            finally:
                s.close()
        if busy:
            raise ModemBusy(_mm_hint(
                f"{busy}/{len(cands)} candidate port(s) could not be opened"))
        raise ModemNotPresent(_mm_hint(
            f"no AT port answered among {cands} (modem powered/registered?)"))

    def open(self) -> None:
        """Open the serial port (autodetecting if port=='auto') and wait until the
        modem answers AT. Raises ModemNotPresent/ModemBusy if it can't."""
        try:
            import serial  # noqa: F401
        except ImportError as e:
            raise ModemNotPresent(f"pyserial not installed ({e}); `pip install pyserial`")
        port = self.port
        if port.lower() == "auto":
            port = self.autodetect(self.baud)
        elif not os.path.exists(port):
            raise ModemNotPresent(f"{port} not present (modem attached?)")
        try:
            self.ser = self._open_serial(port, self.baud, 0.2)
        except Exception as e:
            raise ModemBusy(_mm_hint(f"cannot open {port}: {e}"))
        self.port = port
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        if not self._wait_ready():
            self.close()
            raise ModemNotPresent(_mm_hint(f"{port} opened but modem not responding to AT"))

    def _wait_ready(self, tries: int = 5) -> bool:
        """Poll AT until the modem answers OK (it may still be booting)."""
        for _ in range(tries):
            try:
                self._raw_at("AT", expect=("OK",), timeout=1.5)
                return True
            except ModemError:
                time.sleep(0.5)
        return False

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def __enter__(self):
        self.open()
        self.bring_up()
        return self

    def __exit__(self, *exc):
        try:
            self.mqtt_disconnect()
        finally:
            self.close()
        return False

    # ---- AT primitives ---------------------------------------------------- #
    def _read_until(self, tokens, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        buf = ""
        while time.monotonic() < deadline:
            chunk = self.ser.read(256).decode(errors="ignore")
            if chunk:
                buf += chunk
                if any(t in buf for t in tokens):
                    return buf
            else:
                time.sleep(0.02)
        return buf

    def _raw_at(self, cmd: str, *, expect=("OK",),
                error=("ERROR", "+CME ERROR", "+CMS ERROR"),
                timeout: float = 5.0, quiet: bool = False) -> str:
        """Send one AT command and wait for a terminating token. No retries."""
        if self.ser is None:
            raise ModemNotPresent("serial port not open")
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode())
        resp = self._read_until(tuple(expect) + tuple(error), timeout)
        if not quiet:
            log.debug("AT %-26s -> %s", cmd, resp.replace("\r", " ").strip())
        if any(e in resp for e in error):
            raise ModemError(f"{cmd!r} -> {resp.strip()!r}")
        if not any(x in resp for x in expect):
            raise ModemError(f"{cmd!r} timed out (got {resp.strip()!r})")
        return resp

    def at(self, cmd: str, *, expect=("OK",), timeout: float = 5.0,
           retries: int = 0, quiet: bool = False) -> str:
        """Send an AT command, retrying transient failures `retries` times.

        Use retries only for idempotent queries — NEVER for CMQTTPUB and friends,
        where a blind retry could double-send (the app-level spool handles those)."""
        last = None
        for attempt in range(retries + 1):
            try:
                return self._raw_at(cmd, expect=expect, timeout=timeout, quiet=quiet)
            except ModemError as e:
                last = e
                if attempt < retries:
                    time.sleep(0.4)
        raise last

    def at_prompt(self, cmd: str, data: str, *, expect=("OK",), timeout: float = 5.0) -> str:
        """Prompt pattern: send `cmd`, wait for '>', write exactly `data`, await `expect`.
        Used by CMQTTTOPIC / CMQTTPAYLOAD (which take a byte count then the bytes)."""
        if self.ser is None:
            raise ModemNotPresent("serial port not open")
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode())
        pre = self._read_until((">", "ERROR"), timeout)
        if "ERROR" in pre or ">" not in pre:
            raise ModemError(f"{cmd!r} no '>' prompt (got {pre.strip()!r})")
        self.ser.write(data.encode())            # exactly len(data) bytes, no CR
        resp = self._read_until(tuple(expect) + ("ERROR",), timeout)
        if "ERROR" in resp or not any(x in resp for x in expect):
            raise ModemError(f"{cmd!r} payload not accepted (got {resp.strip()!r})")
        return resp

    # ---- parsers ---------------------------------------------------------- #
    @staticmethod
    def _field(resp: str, prefix: str, index: int):
        """Return the `index`-th comma field after a line starting with `prefix`."""
        for line in resp.splitlines():
            line = line.strip()
            if line.startswith(prefix):
                parts = line.split(":", 1)[1].split(",")
                if len(parts) > index:
                    return parts[index].strip().strip('"')
        return None

    @classmethod
    def _cereg_stat(cls, resp: str):
        v = cls._field(resp, "+CEREG:", 1)
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    @classmethod
    def _csq_dbm(cls, resp: str):
        """CSQ 'rssi' -> dBm, or None if unknown (99)."""
        v = cls._field(resp, "+CSQ:", 0)
        try:
            rssi = int(v)
        except (TypeError, ValueError):
            return None
        return None if rssi == 99 else -113 + 2 * rssi

    @classmethod
    def _cgpaddr_ip(cls, resp: str):
        ip = cls._field(resp, "+CGPADDR:", 1)
        if ip and not ip.startswith("0.0.0.0"):
            return ip
        return None

    @staticmethod
    def _cmqtt_rc(resp: str, prefix: str):
        """Second number of an async CMQTT URC, e.g. '+CMQTTCONNECT: 0,0' -> 0.
        0 means success; anything else is the modem's error code."""
        for line in resp.splitlines():
            line = line.strip()
            if line.startswith(prefix):
                parts = line.split(":", 1)[1].split(",")
                if len(parts) >= 2:
                    try:
                        return int(parts[1].strip())
                    except ValueError:
                        return None
        return None

    # ---- network bring-up ------------------------------------------------- #
    def bring_up(self, *, reg_timeout: float = 60.0, sim_tries: int = 6) -> None:
        """SIM ready -> LTE lock -> registered -> data bearer up. Raises on failure.

        Robust to transient SIM-read delays and the network pre-activating the
        default PDP context. Verifies data via a real IP (CGPADDR), not CGACT's
        say-so."""
        self.at("AT", timeout=3.0, retries=2)
        self.at("ATE0", timeout=3.0, retries=1)      # echo off — clean parsing
        self.at("AT+CMEE=2", timeout=3.0, retries=1) # verbose +CME errors
        self.at("AT+CFUN=1", timeout=10.0)           # full functionality (radio on)

        # SIM: tolerate a few "not ready yet" reads before declaring failure.
        for i in range(sim_tries):
            try:
                resp = self.at("AT+CPIN?", expect=("+CPIN:", "+CME ERROR"),
                               timeout=5.0, quiet=True)
            except ModemError:
                resp = ""
            if "READY" in resp:
                break
            if i == sim_tries - 1:
                raise SimError(_mm_hint(f"SIM not READY after {sim_tries} tries "
                                        f"(last: {resp.strip()!r})"))
            time.sleep(2.0)

        if self.lte_only:
            self.at("AT+CNMP=38", timeout=5.0, retries=1)   # LTE-only; UK 3G retiring

        # Registration: +CEREG stat 1=home, 5=roaming. 3=denied (fatal).
        deadline = time.monotonic() + reg_timeout
        stat = None
        while time.monotonic() < deadline:
            resp = self.at("AT+CEREG?", expect=("+CEREG:",), timeout=5.0,
                           retries=1, quiet=True)
            stat = self._cereg_stat(resp)
            if stat in (1, 5):
                break
            if stat == 3:
                raise RegistrationError(_mm_hint(
                    "+CEREG: 3 registration DENIED (SIM/APN/plan)"))
            time.sleep(2.0)
        if stat not in (1, 5):
            raise RegistrationError(f"not registered on LTE within {reg_timeout:.0f}s "
                                    f"(last +CEREG stat={stat})")

        dbm = self._csq_dbm(self.at("AT+CSQ", expect=("+CSQ:",), timeout=5.0, retries=1))
        if dbm is None:
            raise RegistrationError(_mm_hint("no signal (+CSQ: 99) despite registration"))
        log.info("registered on LTE (%s), signal %d dBm", "roaming" if stat == 5 else "home", dbm)

        # Data bearer: set APN, activate (tolerant), then require a real IP.
        self.at(f'AT+CGDCONT=1,"IP","{self.apn}"', timeout=5.0, retries=1)
        try:
            self.at("AT+CGACT=1,1", timeout=30.0)
        except ModemError as e:
            log.info("CGACT returned %s; the network may have pre-activated it", e)
        ip = self._cgpaddr_ip(self.at("AT+CGPADDR=1", expect=("+CGPADDR:",),
                                      timeout=5.0, retries=1))
        if not ip:
            raise RegistrationError("no PDP address on context 1 (data bearer down)")
        log.info("data bearer up, IP %s", ip)

    def status(self) -> dict:
        """Best-effort health snapshot for diagnostics/heartbeat. Never raises."""
        out = {"operator": None, "act": None, "signal_dbm": None,
               "reg": None, "attached": None, "ip": None}
        probes = (
            ("operator", "AT+COPS?", "+COPS:", 2),
            ("act",      "AT+COPS?", "+COPS:", 3),
            ("reg",      "AT+CEREG?", None, None),
            ("attached", "AT+CGATT?", "+CGATT:", 0),
        )
        for key, cmd, prefix, idx in probes:
            try:
                r = self.at(cmd, expect=(prefix or "+",), timeout=4.0, quiet=True)
                if key == "reg":
                    out[key] = self._cereg_stat(r)
                else:
                    out[key] = self._field(r, prefix, idx)
            except ModemError:
                pass
        try:
            out["signal_dbm"] = self._csq_dbm(
                self.at("AT+CSQ", expect=("+CSQ:",), timeout=4.0, quiet=True))
        except ModemError:
            pass
        try:
            out["ip"] = self._cgpaddr_ip(
                self.at("AT+CGPADDR=1", expect=("+CGPADDR:",), timeout=4.0, quiet=True))
        except ModemError:
            pass
        return out

    def radio_off(self) -> None:
        """Drop the radio to idle (CFUN=0) to save power between windows."""
        try:
            self.at("AT+CFUN=0", timeout=10.0)
            log.info("radio off (CFUN=0)")
        except ModemError as e:
            log.warning("could not turn radio off: %s", e)

    def reset(self, *, wait: float = 25.0) -> None:
        """Full modem reset (CFUN=1,1). Re-enumerates USB, so this CLOSES the port;
        the caller must open() again afterwards. Use as a last-resort self-heal."""
        try:
            if self.ser is not None:
                self.ser.write(b"AT+CFUN=1,1\r")
                time.sleep(1.0)
        except Exception:
            pass
        self.close()
        log.info("modem reset (CFUN=1,1); waiting %.0fs for re-enumeration", wait)
        time.sleep(wait)

    # ---- MQTT (SIMCom CMQTT stack) ---------------------------------------- #
    def mqtt_connect(self, host: str, port: int, client_id: str, *,
                     username: str = "", password: str = "",
                     keepalive: int = 60, clean: int = 1, ssl: bool = False) -> None:
        """Start the CMQTT stack and connect. Tracks state for clean teardown."""
        # START (idempotent: if the stack was left started, proceed anyway).
        try:
            self.at("AT+CMQTTSTART", expect=("+CMQTTSTART: 0", "OK"), timeout=12.0)
        except ModemError as e:
            log.info("CMQTTSTART said %s; assuming already started", e)
        self._started = True

        self.at(f'AT+CMQTTACCQ=0,"{client_id}",{1 if ssl else 0}', timeout=5.0)
        self._acquired = True

        scheme = "ssl" if ssl else "tcp"
        url = f"{scheme}://{host}:{port}"
        if username:
            cmd = f'AT+CMQTTCONNECT=0,"{url}",{keepalive},{clean},"{username}","{password}"'
        else:
            cmd = f'AT+CMQTTCONNECT=0,"{url}",{keepalive},{clean}'
        # Async: OK, then +CMQTTCONNECT: 0,<rc>. rc 0 == success.
        resp = self.at(cmd, expect=("+CMQTTCONNECT: 0,",), timeout=25.0)
        rc = self._cmqtt_rc(resp, "+CMQTTCONNECT:")
        if rc != 0:
            raise MqttError(f"MQTT connect failed to {url} (rc={rc})")
        self._connected = True
        log.info("MQTT connected to %s", url)

    def mqtt_publish(self, topic: str, payload: str, *, qos: int = 1,
                     pub_timeout: int = 60) -> None:
        """Publish one message. Raises MqttError on any non-success result."""
        if not self._connected:
            raise MqttError("publish attempted with no MQTT connection")
        self.at_prompt(f"AT+CMQTTTOPIC=0,{len(topic)}", topic, timeout=5.0)
        self.at_prompt(f"AT+CMQTTPAYLOAD=0,{len(payload.encode())}", payload, timeout=5.0)
        resp = self.at(f"AT+CMQTTPUB=0,{qos},{pub_timeout}",
                       expect=("+CMQTTPUB: 0,",), timeout=max(20, pub_timeout))
        rc = self._cmqtt_rc(resp, "+CMQTTPUB:")
        if rc != 0:
            raise MqttError(f"publish rc={rc} for topic {topic!r}")

    def mqtt_disconnect(self) -> None:
        """Tear down whatever is up, in order. Idempotent and never raises."""
        steps = []
        if self._connected:
            steps.append(("AT+CMQTTDISC=0,60", ("+CMQTTDISC: 0,0", "OK")))
        if self._acquired:
            steps.append(("AT+CMQTTREL=0", ("OK",)))
        if self._started:
            steps.append(("AT+CMQTTSTOP", ("+CMQTTSTOP: 0", "OK")))
        for cmd, exp in steps:
            try:
                self.at(cmd, expect=exp, timeout=10.0)
            except ModemError as e:
                log.warning("teardown step %s: %s", cmd, e)
        self._connected = self._acquired = self._started = False


# --------------------------------------------------------------------------- #
# CLI: python3 cell_modem.py  → open, bring up, print a status snapshot.
# Handy for field diagnostics without touching the sender/service.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="A7670E cellular modem probe")
    ap.add_argument("--port", default=os.environ.get("YEO_MODEM_PORT", "auto"))
    ap.add_argument("--apn", default=os.environ.get("YEO_APN", "key"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    m = CellModem(port=args.port, apn=args.apn)
    try:
        m.open()
        m.bring_up()
        print(json.dumps(m.status(), indent=2))
    except ModemError as e:
        print(f"MODEM ERROR: {e}")
        raise SystemExit(1)
    finally:
        m.close()
