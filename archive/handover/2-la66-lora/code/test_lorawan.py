"""Hardware-free tests for the LA66 LoRaWAN driver.

A FakeLink emulates the module: a `responder(cmd) -> list[str]` maps each AT
command to the reply lines the LA66 would send, so the whole driver (reader
thread, AT parsing, join polling, duty cycle, send/txDone) runs with no radio.

Run:  ../../cellular/.venv/bin/python -m pytest test_lorawan.py -v
"""

import time

import pytest

from lorawan import (
    DR_TO_SF,
    LA66,
    Link,
    LoRaJoinError,
    LoRaModuleError,
    LoRaTimeout,
    build_beacon,
    lora_airtime_s,
)


class FakeLink(Link):
    """In-memory Link: send() maps the command to canned reply bytes that
    recv() then yields, driving the real reader thread."""

    def __init__(self, responder):
        self.responder = responder
        self._buf = bytearray()
        self._open = False
        self.sent: list[str] = []

    def open(self) -> None:
        self._open = True

    def send(self, data: bytes) -> None:
        if not self._open:
            from lorawan import LoRaConnectionError
            raise LoRaConnectionError("fake link not open")
        cmd = data.decode("ascii", "ignore").strip()
        self.sent.append(cmd)
        for line in self.responder(cmd):
            self._buf += (line + "\r\n").encode()

    def recv(self, timeout: float) -> bytes:
        if self._buf:
            out = bytes(self._buf)
            self._buf.clear()
            return out
        time.sleep(min(timeout, 0.01))
        return b""

    def close(self) -> None:
        self._open = False


def make(responder, **kw) -> LA66:
    kw.setdefault("respect_duty_cycle", False)  # no real sleeps in tests
    kw.setdefault("warm_up", False)             # skip the warm-up delay
    la = LA66(FakeLink(responder), **kw)
    la.connect()
    return la


# --- basic AT + airtime ----------------------------------------------------- #

def test_at_ok():
    la = make(lambda c: ["OK"])
    assert la.at("AT") == ["OK"]
    assert "AT" in la.link.sent


def test_at_error_raises():
    la = make(lambda c: ["AT_ERROR"])
    with pytest.raises(LoRaModuleError):
        la.at("AT+BAD")


def test_at_timeout_raises():
    la = make(lambda c: [])  # module says nothing
    with pytest.raises(LoRaTimeout):
        la.at("AT", timeout=0.1)


def test_airtime_sf12_longer_than_sf7():
    assert lora_airtime_s(7, sf=12) > lora_airtime_s(7, sf=7)


def test_build_beacon_shape():
    assert build_beacon(1) == b"\xac\x51\x01\x00\x00\x00\x00"


# --- provisioning + OTAA join ---------------------------------------------- #

def test_provision_otaa_sets_mode_and_keys():
    la = make(lambda c: ["OK"])
    la.provision_otaa(
        dev_eui="A840414A655D113C",
        app_key="D00A46A4E9A669D19CB3B8641FCE2C1F",
    )
    sent = la.link.sent
    assert "AT+NJM=1" in sent
    assert "AT+DEUI=A840414A655D113C" in sent
    assert "AT+APPEUI=0000000000000000" in sent  # default JoinEUI
    assert "AT+APPKEY=D00A46A4E9A669D19CB3B8641FCE2C1F" in sent


def test_provision_rejects_bad_hex():
    la = make(lambda c: ["OK"])
    with pytest.raises(ValueError):
        la.provision_otaa(dev_eui="XYZ", app_key="00")


def test_provision_abp_sets_mode_and_keys():
    la = make(lambda c: ["OK"])
    la.provision_abp(
        dev_addr="1E6E6C89",
        nwkskey="12532281668F665FD5F9AA86CBFC2027",
        appskey="D00A46A4E9A669D19CB3B8641FCE2C1F",
    )
    assert "AT+NJM=0" in la.link.sent
    assert "AT+DADDR=1E6E6C89" in la.link.sent


def test_join_succeeds_when_njs_reaches_1():
    """AT+NJS=0 at first, then 1 after AT+JOIN — join() must poll to success."""
    state = {"joined": False}

    def responder(cmd):
        if cmd == "AT+JOIN":
            state["joined"] = True
            return ["OK"]
        if cmd.startswith("AT+NJS"):
            return ["1" if state["joined"] else "0", "OK"]
        return ["OK"]

    la = make(responder)
    la.join(timeout=2, poll=0.01)          # must not raise
    assert la.joined() is True


def test_join_raises_when_never_joined():
    def responder(cmd):
        if cmd.startswith("AT+NJS"):
            return ["0", "OK"]             # never joins
        return ["OK"]

    la = make(responder)
    with pytest.raises(LoRaJoinError):
        la.join(timeout=0.2, poll=0.01, retries=1)


def test_join_noop_if_already_joined():
    la = make(lambda c: ["1", "OK"] if c.startswith("AT+NJS") else ["OK"])
    la.join(timeout=1, poll=0.01)
    assert "AT+JOIN" not in la.link.sent   # already joined -> no join issued


# --- configure posture ------------------------------------------------------ #

def test_full_channel_plan_defaults_adr_on_and_resets():
    seen = []
    la = make(lambda c: (seen.append(c) or (["OK"])))
    la.configure(full_channel_plan=True)
    assert "AT+CHS=0" in seen
    assert "AT+ADR=1" in seen                # full plan -> ADR on by default
    assert "ATZ" in seen                     # CHS change -> module reset


def test_single_channel_forces_adr_off_and_resets():
    seen = []
    la = make(lambda c: (seen.append(c) or ["OK"]))
    la.configure(single_channel_hz=868100000, dr=0)
    assert "AT+CHS=868100000" in seen
    assert "AT+ADR=0" in seen                # fixed channel -> ADR forced off
    assert "AT+DR=0" in seen
    assert "ATZ" in seen
    assert la.sf == DR_TO_SF[0]              # airtime model updated to SF12


def test_dr_only_change_does_not_reset():
    seen = []
    la = make(lambda c: (seen.append(c) or ["OK"]))
    la.configure(dr=5)
    assert "AT+DR=5" in seen
    assert "ATZ" not in seen                 # no channel change -> no reset
    assert la.sf == DR_TO_SF[5]


# --- send / txDone gating --------------------------------------------------- #

def _send_responder(cmd):
    if cmd.startswith("AT+SENDB"):
        return [
            "***** UpLinkCounter= 7 *****",
            "TX on freq 868.100 MHz at DR 0",
            "OK",
            "txDone",
        ]
    return ["OK"]


def test_send_waits_for_txdone_and_parses():
    la = make(_send_responder)
    res = la.send_beacon(42)
    assert res.ok and res.tx_done
    assert res.fcnt == 7
    assert res.freq_mhz == "868.100"
    assert res.dr == 0
    # confirm flag + payload encoded into AT+SENDB
    sendb = [c for c in la.link.sent if c.startswith("AT+SENDB")][0]
    assert sendb.startswith("AT+SENDB=0,2,7,")   # unconfirmed, fport 2, 7 bytes


def test_confirmed_uplink_sets_flag():
    la = make(_send_responder)
    la.send(build_beacon(1), confirm=True)
    sendb = [c for c in la.link.sent if c.startswith("AT+SENDB")][0]
    assert sendb.startswith("AT+SENDB=1,")       # confirm=1


def test_send_module_error_raises():
    la = make(lambda c: ["AT_NO_NETWORK_JOINED"] if c.startswith("AT+SENDB")
              else ["OK"])
    with pytest.raises(LoRaModuleError):
        la.send(build_beacon(1), retries=0)


def test_send_rejects_bad_payload():
    la = make(_send_responder)
    with pytest.raises(TypeError):
        la.send("not bytes")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        la.send(b"")          # empty


def test_context_manager_closes_link():
    la = make(lambda c: ["OK"])
    link = la.link
    la.close()
    assert link._open is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
