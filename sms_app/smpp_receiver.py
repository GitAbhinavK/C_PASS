#!/usr/bin/env python3
"""
smpp_receiver.py

Patched SMPP receiver that accepts TCP PDUs (deliver_sm) and processes DLRs.
Sends a cleaned dict to your `handle_deliver_sm(pdu_dict)` handler.

Usage:
    DJANGO_SETTINGS_MODULE=sms_project.settings python smpp_receiver.py
"""

import os
import struct
import socket
import threading
import logging
import re
from typing import Tuple, Dict, Optional

# --- Configure Django environment (adjust module path as needed) ---
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sms_project.settings")
import django
django.setup()

from django.utils import timezone

# Import your existing DLR handler (update path if different)
try:
    from sms_app.deliver_sm import handle_deliver_sm
except Exception as e:
    raise RuntimeError("Failed import sms_app.deliver_sm.handle_deliver_sm: " + str(e))

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("smpp_receiver")

# SMPP command IDs (as numeric values)
CMD_ID_MAP = {
    0x00000005: "deliver_sm",
    0x00000004: "submit_sm",
    0x80000004: "submit_sm_resp",
    0x80000005: "deliver_sm_resp",
}

# Helper read exact bytes from socket
def recv_exact(sock: socket.socket, n: int) -> bytes:
    b = bytearray()
    while len(b) < n:
        chunk = sock.recv(n - len(b))
        if not chunk:
            raise ConnectionError("Socket closed while reading")
        b.extend(chunk)
    return bytes(b)

# Parse SMPP header (4 x 4 bytes big-endian ints)
def parse_header(pdu: bytes) -> Tuple[int,int,int,int]:
    if len(pdu) < 16:
        raise ValueError("PDU too short for header")
    cmd_len, cmd_id, cmd_status, seq = struct.unpack(">IIII", pdu[:16])
    return cmd_len, cmd_id, cmd_status, seq

# Read C-string from bytes starting at offset; return (string, new_offset)
def parse_cstring(pdu: bytes, offset: int) -> Tuple[str,int]:
    end = pdu.find(b"\x00", offset)
    if end == -1:
        raise ValueError("C-string not terminated")
    raw = pdu[offset:end]
    try:
        return raw.decode("ascii", errors="ignore"), end + 1
    except Exception:
        return raw.decode("latin1", errors="ignore"), end + 1

# Parse TLVs present after short_message
def parse_tlvs(pdu: bytes, offset: int) -> Dict[int, bytes]:
    tlvs = {}
    while offset + 4 <= len(pdu):
        tag, length = struct.unpack(">HH", pdu[offset:offset+4])
        offset += 4
        if offset + length > len(pdu):
            # malformed TLV - break
            break
        value = pdu[offset: offset + length]
        tlvs[tag] = value
        offset += length
    return tlvs

# Clean an extracted message id to safe format (allow alnum and hyphen)
def clean_message_id(raw: str) -> Optional[str]:
    if not raw:
        return None
    # remove surrounding whitespace and control chars
    s = raw.strip()
    # Some systems may prefix with "id:" or "receipted_message_id=" etc. Extract possible id substring
    # Common patterns: id:12345, id=12345, receipted_message_id:xxxx
    m = re.search(r"(?:id[:=]\s*|receipted_message_id[:=]\s*|message_id[:=]\s*)?([A-Za-z0-9\-]{6,200})", s, re.IGNORECASE)
    if m:
        candidate = m.group(1)
    else:
        # fallback: keep only allowed chars
        candidate = "".join(ch for ch in s if ch.isalnum() or ch == "-")
    candidate = candidate.strip("-_")
    return candidate if candidate else None

# Try various sources to obtain receipted_message_id
def extract_receipted_message_id(parsed: Dict) -> Optional[str]:
    """
    parsed: result of parse_deliver_sm_pdu
    Search order:
     1) TLV 0x001E (receipted_message_id)
     2) TLV 0x0424 (message_payload) if it contains 'id:' pattern
     3) short_message text (regex for id:... stat:...)
     4) any ascii substring in raw_short_message containing 'id:' or looks like uuid
    """
    tlvs = parsed.get("tlvs", {})
    # 1) TLV 0x001E
    if 0x001E in tlvs:
        try:
            val = tlvs[0x001E]
            # remove trailing nulls
            val = val.split(b'\x00')[0]
            decoded = val.decode("ascii", errors="ignore")
            cleaned = clean_message_id(decoded)
            if cleaned:
                return cleaned
        except Exception:
            pass

    # 2) message_payload TLV (0x0424) or other textual TLVs
    for tag in (0x0424,):  # extend list if needed
        if tag in tlvs:
            try:
                val = tlvs[tag].split(b'\x00')[0]
                decoded = val.decode("ascii", errors="ignore")
                # look for id: ... stat: ...
                m = re.search(r"id[:=]\s*([A-Za-z0-9\-]+)", decoded, re.IGNORECASE)
                if m:
                    cleaned = clean_message_id(m.group(1))
                    if cleaned:
                        return cleaned
                # fallback to any uuid-like substring
                m2 = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", decoded, re.IGNORECASE)
                if m2:
                    return m2.group(0)
            except Exception:
                pass

    # 3) short_message text
    sm = parsed.get("short_message", "") or ""
    if sm:
        # common DLR format: "id:... sub:... dlvrd:... stat:DELIVRD err:000 text:..."
        m = re.search(r"id[:=]\s*([A-Za-z0-9\-]+)", sm, re.IGNORECASE)
        if m:
            cleaned = clean_message_id(m.group(1))
            if cleaned:
                return cleaned
        # uuid-like inside short_message
        m2 = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", sm, re.IGNORECASE)
        if m2:
            return m2.group(0)

    # 4) raw_short_message bytes: attempt to decode and search
    raw = parsed.get("short_message_raw", b"")
    if raw:
        try:
            dec = raw.decode("ascii", errors="ignore")
        except Exception:
            dec = raw.decode("latin1", errors="ignore")
        m = re.search(r"id[:=]\s*([A-Za-z0-9\-]+)", dec, re.IGNORECASE)
        if m:
            cleaned = clean_message_id(m.group(1))
            if cleaned:
                return cleaned
        m2 = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", dec, re.IGNORECASE)
        if m2:
            return m2.group(0)

    return None

# Main deliver_sm PDU parser: returns dict with decoded items
def parse_deliver_sm_pdu(pdu_bytes: bytes) -> Dict:
    cmd_len, cmd_id, cmd_status, seq = parse_header(pdu_bytes)
    offset = 16

    # service_type (C-string)
    service_type, offset = parse_cstring(pdu_bytes, offset)

    # source_addr_ton (1), source_addr_npi (1), source_addr (C-string)
    if offset + 2 > len(pdu_bytes):
        raise ValueError("Truncated before source addr fields")
    offset += 2
    source_addr, offset = parse_cstring(pdu_bytes, offset)

    # dest_addr_ton (1), dest_addr_npi (1), destination_addr (C-string)
    if offset + 2 > len(pdu_bytes):
        raise ValueError("Truncated before dest addr fields")
    offset += 2
    dest_addr, offset = parse_cstring(pdu_bytes, offset)

    # esm_class, protocol_id, priority_flag
    if offset + 3 > len(pdu_bytes):
        raise ValueError("PDU truncated before esm_class fields")
    esm_class = pdu_bytes[offset]
    protocol_id = pdu_bytes[offset + 1]
    priority_flag = pdu_bytes[offset + 2]
    offset += 3

    # schedule_delivery_time (C-string)
    schedule_delivery_time, offset = parse_cstring(pdu_bytes, offset)
    # validity_period (C-string)
    validity_period, offset = parse_cstring(pdu_bytes, offset)

    # registered_delivery, replace_if_present_flag, data_coding, sm_default_msg_id, sm_length
    if offset + 5 > len(pdu_bytes):
        raise ValueError("PDU truncated before sm_length")
    registered_delivery = pdu_bytes[offset]
    replace_if_present_flag = pdu_bytes[offset + 1]
    data_coding = pdu_bytes[offset + 2]
    sm_default_msg_id = pdu_bytes[offset + 3]
    sm_length = pdu_bytes[offset + 4]
    offset += 5

    # short_message: next sm_length bytes (may be truncated)
    if sm_length > 0:
        if offset + sm_length > len(pdu_bytes):
            short_message_raw = pdu_bytes[offset:]
            offset = len(pdu_bytes)
        else:
            short_message_raw = pdu_bytes[offset: offset + sm_length]
            offset += sm_length
    else:
        short_message_raw = b""

    # parse TLVs if present
    tlvs = {}
    if offset < len(pdu_bytes):
        tlvs = parse_tlvs(pdu_bytes, offset)

    # decode short_message best-effort
    try:
        short_message = short_message_raw.decode("ascii", errors="ignore")
    except Exception:
        short_message = short_message_raw.decode("latin1", errors="ignore")

    return {
        "sequence_number": seq,
        "command_id": cmd_id,
        "command_status": cmd_status,
        "esm_class": esm_class,
        "registered_delivery": registered_delivery,
        "data_coding": data_coding,
        "short_message_raw": short_message_raw,
        "short_message": short_message,
        "sm_length": sm_length,
        "tlvs": tlvs,
        "source_addr": source_addr,
        "dest_addr": dest_addr,
        "received_at": timezone.now(),
    }

# Top-level PDU processor for incoming PDUs
def process_pdu_bytes(pdu_bytes: bytes):
    try:
        header_len, cmd_id, cmd_status, seq = parse_header(pdu_bytes)
    except Exception as e:
        log.error("Malformed PDU header: %s", e)
        return

    human_cmd = CMD_ID_MAP.get(cmd_id, f"0x{cmd_id:x}")
    log.info("Received PDU: cmd=%s seq=%s len=%s", human_cmd, seq, header_len)

    if cmd_id == 0x00000005:  # deliver_sm
        try:
            parsed = parse_deliver_sm_pdu(pdu_bytes)
        except Exception as e:
            log.exception("Failed to parse deliver_sm PDU: %s", e)
            return

        # decode TLVs to ascii strings for convenience
        decoded_tlvs = {}
        for tag, val in parsed.get("tlvs", {}).items():
            try:
                decoded = val.split(b'\x00')[0].decode("ascii", errors="ignore")
            except Exception:
                decoded = val.split(b'\x00')[0].decode("latin1", errors="ignore")
            decoded_tlvs[tag] = decoded

        # attempt to extract receipted_message_id
        receipted_id = extract_receipted_message_id(parsed)

        # build clean short_message: if TLV contains full DLR, prefer that
        short_msg = parsed.get("short_message", "") or ""
        if not short_msg:
            # prefer TLV message_payload if present
            for key in (0x0424,):
                if key in parsed.get("tlvs", {}):
                    try:
                        short_msg = parsed["tlvs"][key].split(b'\x00')[0].decode("ascii", errors="ignore")
                        break
                    except Exception:
                        pass
            # last resort: ascii decode of raw message
            if not short_msg:
                try:
                    raw = parsed.get("short_message_raw", b"")
                    short_msg = raw.decode("ascii", errors="ignore")
                except Exception:
                    short_msg = ""

        # Extract DLR status and err if present
        dlr_stat = None
        dlr_err = None
        if short_msg:
            m_stat = re.search(r"stat[:=]\s*([A-Z]+)", short_msg, re.IGNORECASE)
            if m_stat:
                dlr_stat = m_stat.group(1).upper()
            m_err = re.search(r"err[:=]\s*([0-9]+)", short_msg, re.IGNORECASE)
            if m_err:
                dlr_err = m_err.group(1)

        # Compose the pdu_dict to pass to your handler
        pdu_dict = {
            "sequence_number": parsed.get("sequence_number"),
            "command_status": parsed.get("command_status"),
            "short_message": short_msg,
            "raw_short_message": parsed.get("short_message_raw"),
            "tlvs": decoded_tlvs,  # string-decoded TLVs
            "tlvs_raw": parsed.get("tlvs"),  # raw bytes TLVs if you need them
            "source_addr": parsed.get("source_addr"),
            "dest_addr": parsed.get("dest_addr"),
            "data_coding": parsed.get("data_coding"),
            "received_at": parsed.get("received_at"),
            # extracted cleaner fields
            "receipted_message_id": receipted_id,
            "dlr_stat": dlr_stat,
            "dlr_err": dlr_err,
        }

        log.info("Deliver_SM: src=%s dst=%s seq=%s id=%s stat=%s tlv_tags=%s",
                 pdu_dict["source_addr"], pdu_dict["dest_addr"],
                 pdu_dict["sequence_number"], pdu_dict["receipted_message_id"],
                 pdu_dict["dlr_stat"], list(decoded_tlvs.keys()))

        # Call your handler (expects dict)
        try:
            handle_deliver_sm(pdu_dict)
        except Exception as e:
            log.exception("Error when calling handle_deliver_sm: %s", e)

    else:
        log.info("Unhandled PDU type: %s (cmd_id=0x%08x)", human_cmd, cmd_id)

# Server connection handler
def handle_client(sock: socket.socket, peer: Tuple[str,int]):
    log.info("Client connected: %s", peer)
    try:
        while True:
            # Read 4 bytes first for command_length
            header = sock.recv(4)
            if not header:
                log.info("Client disconnected: %s", peer)
                break
            if len(header) < 4:
                header += recv_exact(sock, 4 - len(header))

            (cmd_len,) = struct.unpack(">I", header)
            if cmd_len <= 0:
                log.warning("Invalid cmd_len %s from %s", cmd_len, peer)
                break

            remaining = cmd_len - 4
            pdu_rest = recv_exact(sock, remaining)
            pdu_bytes = header + pdu_rest

            try:
                process_pdu_bytes(pdu_bytes)
            except Exception as e:
                log.exception("Error processing PDU: %s", e)

    except ConnectionError as e:
        log.info("Connection error with %s: %s", peer, e)
    except Exception as e:
        log.exception("Unhandled client error: %s", e)
    finally:
        try:
            sock.close()
        except Exception:
            pass
        log.info("Client handler exiting: %s", peer)

# TCP listener
def run_server(host="0.0.0.0", port=2776):
    log.info("Starting SMPP receiver on %s:%d", host, port)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    try:
        while True:
            client_sock, addr = srv.accept()
            t = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        log.info("Shutting down SMPP receiver")
    finally:
        srv.close()

if __name__ == "__main__":
    run_server(host="0.0.0.0", port=2776)
