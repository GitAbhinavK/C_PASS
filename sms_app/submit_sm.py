import struct
import socket
import re
import logging
import time
from typing import Dict, Optional, Tuple, List

# Import your TLV utilities
from .tlv_utils import encode_tlvs, tagname_to_int

log = logging.getLogger("submit_sm")
if not log.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

# -------------------------------------------------
# GSM 03.38 (safe subset)
# -------------------------------------------------
GSM0338_MAP = {
    '@': 0x00, '£': 0x01, '$': 0x02, '¥': 0x03,
    'è': 0x04, 'é': 0x05, 'ù': 0x06, 'ì': 0x07,
    'ò': 0x08, 'Ç': 0x09, '\n': 0x0A, '\r': 0x0D,
    'Å': 0x0E, 'å': 0x0F, 'Δ': 0x10, '_': 0x11,
    'Φ': 0x12, 'Γ': 0x13, 'Λ': 0x14, 'Ω': 0x15,
    ' ': 0x20, '!': 0x21, '"': 0x22, '#': 0x23,
    '¤': 0x24, '%': 0x25, '&': 0x26, "'": 0x27,
    '(': 0x28, ')': 0x29, '*': 0x2A, '+': 0x2B,
    ',': 0x2C, '-': 0x2D, '.': 0x2E, '/': 0x2F,
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33,
    '4': 0x34, '5': 0x35, '6': 0x36, '7': 0x37,
    '8': 0x38, '9': 0x39, ':': 0x3A, ';': 0x3B,
    '<': 0x3C, '=': 0x3D, '>': 0x3E, '?': 0x3F,
    '¡': 0x40, 'A': 0x41, 'B': 0x42, 'C': 0x43,
    'D': 0x44, 'E': 0x45, 'F': 0x46, 'G': 0x47,
    'H': 0x48, 'I': 0x49, 'J': 0x4A, 'K': 0x4B,
    'L': 0x4C, 'M': 0x4D, 'N': 0x4E, 'O': 0x4F,
    'P': 0x50, 'Q': 0x51, 'R': 0x52, 'S': 0x53,
    'T': 0x54, 'U': 0x55, 'V': 0x56, 'W': 0x57,
    'X': 0x58, 'Y': 0x59, 'Z': 0x5A, 'Ä': 0x5B,
    'Ö': 0x5C, 'Ñ': 0x5D, 'Ü': 0x5E, '§': 0x5F,
    '¿': 0x60, 'a': 0x61, 'b': 0x62, 'c': 0x63,
    'd': 0x64, 'e': 0x65, 'f': 0x66, 'g': 0x67,
    'h': 0x68, 'i': 0x69, 'j': 0x6A, 'k': 0x6B,
    'l': 0x6C, 'm': 0x6D, 'n': 0x6E, 'o': 0x6F,
    'p': 0x70, 'q': 0x71, 'r': 0x72, 's': 0x73,
    't': 0x74, 'u': 0x75, 'v': 0x76, 'w': 0x77,
    'x': 0x78, 'y': 0x79, 'z': 0x7A, 'ä': 0x7B,
    'ö': 0x7C, 'ñ': 0x7D, 'ü': 0x7E, 'à': 0x7F
}

def encode_gsm_7bit_packed(text: str) -> Optional[bytes]:
    """Encode text to GSM 03.38 7-bit packed format"""
    if not text:
        return b''

    septets = []
    for ch in text:
        if ch not in GSM0338_MAP:
            return None
        septets.append(GSM0338_MAP[ch])

    packed = bytearray()
    carry = 0
    carry_bits = 0

    for s in septets:
        carry |= s << carry_bits
        carry_bits += 7
        while carry_bits >= 8:
            packed.append(carry & 0xFF)
            carry >>= 8
            carry_bits -= 8

    if carry_bits:
        packed.append(carry & 0xFF)

    return bytes(packed)

# -------------------------------------------------
# SMS Sender with DLR SUPPORT
# -------------------------------------------------
class SMSSender:
    """SMPP SMS Sender with proper DLR request configuration"""

    def __init__(self, client):
        self.client = client

    def _recv_exact(self, sock: socket.socket, n: int, timeout: int = 30) -> bytes:
        """Receive exactly n bytes from socket"""
        sock.settimeout(timeout)
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Socket closed by SMSC")
            data += chunk
        return data

    def _extract_message_id(self, body: bytes) -> Optional[str]:
        """Extract message ID from submit_sm_resp body"""
        try:
            txt = body.decode("ascii", errors="ignore")
        except Exception:
            return None

        m = re.search(r"[A-Za-z0-9\-]{6,200}", txt)
        return m.group(0) if m else None

    def _read_one_pdu(self, timeout: float = 20) -> Tuple[int, int, int, bytes]:
        """Read one complete SMPP PDU and return header fields plus body."""
        header = self._recv_exact(self.client.socket, 16, timeout=timeout)
        resp_len, resp_cmd, resp_status, resp_seq = struct.unpack(">IIII", header)
        body = b''
        if resp_len > 16:
            body = self._recv_exact(self.client.socket, resp_len - 16, timeout=timeout)
        return resp_cmd, resp_status, resp_seq, body

    def _build_submit_sm_pdu(
        self,
        source_addr: str,
        destination_addr: str,
        short_message: bytes,
        source_addr_ton: int,
        source_addr_npi: int,
        dest_addr_ton: int,
        dest_addr_npi: int,
        registered_delivery: int,
        data_coding: int,
        service_type: bytes,
        tlv_bytes: bytes
    ) -> Tuple[bytes, int]:
        """Build submit_sm PDU with all required fields"""
        
        command_id = 0x00000004  # submit_sm
        command_status = 0x00000000
        seq = self.client.get_sequence_number()

        log.info(
            "📡 SENDING SMS - Sender: %s | TON: %d | NPI: %d | Registered Delivery: %d",
            source_addr, source_addr_ton, source_addr_npi, registered_delivery
        )

        # Build PDU body
        body = b''.join([
            service_type,
            struct.pack(">BB", source_addr_ton, source_addr_npi),
            source_addr.encode("ascii") + b'\x00',
            struct.pack(">BB", dest_addr_ton, dest_addr_npi),
            destination_addr.encode("ascii") + b'\x00',
            b'\x00',  # esm_class (0 for standard SMS)
            b'\x00',  # protocol_id
            b'\x00',  # priority_flag
            b'\x00',  # schedule_delivery_time
            b'\x00',  # validity_period
            struct.pack(">B", registered_delivery),  # CRITICAL for DLR
            b'\x00',  # replace_if_present_flag
            struct.pack(">B", data_coding),
            b'\x00',  # sm_default_msg_id
            struct.pack(">B", len(short_message)),
            short_message
        ])

        total_len = 16 + len(body) + len(tlv_bytes)
        header = struct.pack(">IIII", total_len, command_id, command_status, seq)

        return header + body + tlv_bytes, seq

    def submit_sm_with_tlvs(
        self,
        source_addr: str,
        destination_addr: str,
        message: str,
        smpp_config: Dict,
        tlvs_map: Optional[Dict] = None
    ) -> Dict:
        """
        Submit SMS with proper DLR request
        
        CRITICAL: registered_delivery must be set to 1 to receive DLRs
        """
        
        # Get TON/NPI from CONFIG
        source_addr_ton = smpp_config.get("source_addr_ton", 5)  # Default to alphanumeric
        source_addr_npi = smpp_config.get("source_addr_npi", 0)  # Default to unknown
        
        log.info(f"📊 Using TON={source_addr_ton}, NPI={source_addr_npi} from smpp_config")

        # ========== CRITICAL FIX FOR DLR ==========
        # registered_delivery MUST be set to 1 to request delivery receipt
        # Bit 0 = 1 means request delivery receipt
        registered_delivery = smpp_config.get("registered_delivery", 1)
        
        # Override to 1 if it's 0 (to ensure DLRs are requested)
        if registered_delivery == 0:
            log.warning("⚠️ registered_delivery is 0 in config, overriding to 1 for DLR")
            registered_delivery = 1
        
        log.info(f"📋 Registered Delivery Flag: {registered_delivery} (1 = Request DLR)")
        # ==========================================

        # Get template_id for TLVs
        template_id = smpp_config.get('template_id')
        if not template_id and tlvs_map:
            template_id = tlvs_map.get(0x1401) or tlvs_map.get('template_id')
        
        # Determine encoding
        has_template = bool(template_id) or '{' in message
        
        if has_template:
            short_message = message.encode("utf-16-be")
            data_coding = 0x08  # UCS-2
            log.info("Template message detected, using UCS-2 encoding")
        else:
            gsm = encode_gsm_7bit_packed(message)
            if gsm is not None:
                short_message = gsm
                data_coding = 0x00  # GSM-7
            else:
                short_message = message.encode("utf-16-be")
                data_coding = 0x08  # UCS-2

        dest_addr_ton = smpp_config.get("dest_addr_ton", 1)
        dest_addr_npi = smpp_config.get("dest_addr_npi", 1)
        service_type = smpp_config.get("service_type", "CMS").encode("ascii") + b'\x00'

        # Prepare TLVs
        numeric_tlvs = {}
        
        # Add PE_ID (0x1400) from smpp_config
        pe_id = smpp_config.get('pe_id')
        if pe_id:
            numeric_tlvs[0x1400] = str(pe_id).encode("ascii")
            log.info("TLV ADDED -> TAG=0x1400 (PE_ID) VALUE=%s", pe_id)
        
        # Add Template ID (0x1401)
        if template_id:
            numeric_tlvs[0x1401] = str(template_id).encode("ascii")
            log.info("TLV ADDED -> TAG=0x1401 (Template ID) VALUE=%s", template_id)
        
        # Add TMID (0x1402) if present
        tmid = None
        if tlvs_map and 0x1402 in tlvs_map:
            tmid = str(tlvs_map[0x1402])
            numeric_tlvs[0x1402] = tmid.encode("ascii")
            log.info("TLV ADDED -> TAG=0x1402 (TMID) VALUE=%s", tmid[:10] + "...")
        
        # Add any additional TLVs
        for k, v in (tlvs_map or {}).items():
            if not v:
                continue
            if isinstance(k, str):
                k = tagname_to_int(k)
            
            tag_int = int(k)
            if tag_int not in numeric_tlvs:
                numeric_tlvs[tag_int] = str(v).encode("ascii")

        tlv_bytes = encode_tlvs(numeric_tlvs)
        
        if numeric_tlvs:
            log.info("Total TLVs sent: %d, Total bytes: %d", len(numeric_tlvs), len(tlv_bytes))

        lock = getattr(self.client, "socket_lock", None)
        seq = None

        try:
            if lock:
                lock.acquire()

            # Ensure connection is active while this thread owns the socket.
            if not self.client.ensure_connected_and_bound():
                return {"status": "failed", "error": "SMPP not connected"}

            # Build PDU with CORRECT TON/NPI and registered_delivery.
            # Sequence generation is kept inside the socket lock.
            pdu, seq = self._build_submit_sm_pdu(
                source_addr,
                destination_addr,
                short_message,
                source_addr_ton,
                source_addr_npi,
                dest_addr_ton,
                dest_addr_npi,
                registered_delivery,  # CRITICAL: This must be 1
                data_coding,
                service_type,
                tlv_bytes
            )

            self.client.socket.sendall(pdu)

            deadline = time.time() + 20
            body = b''
            resp_status = None

            while time.time() < deadline:
                timeout_left = max(0.5, deadline - time.time())
                resp_cmd, resp_status, resp_seq, body = self._read_one_pdu(timeout=timeout_left)

                if resp_cmd == 0x80000004 and resp_seq == seq:
                    break

                # Keep unrelated incoming PDUs for the listener instead of letting
                # submit_sm consume a DLR/enquire_link meant for the receive loop.
                incoming_queue = getattr(self.client, "incoming_pdu_queue", None)
                if incoming_queue is not None:
                    incoming_queue.put({
                        "command_id": resp_cmd,
                        "command_status": resp_status,
                        "sequence_number": resp_seq,
                        "body": body,
                    })
                log.info(
                    "Queued non-submit_sm_resp PDU while waiting for seq %s: cmd=0x%08X seq=%s",
                    seq, resp_cmd, resp_seq
                )
            else:
                return {
                    "status": "failed",
                    "destination_number": destination_addr,
                    "error": "Socket timeout waiting for submit_sm_resp",
                    "sequence_number": seq
                }

            msg_id = self._extract_message_id(body)

            # Check for specific errors
            if resp_status == 0x00000083:  # ESME_RINVSERTON
                return {
                    "status": "failed",
                    "destination_number": destination_addr,
                    "error": f"ERROR 083: Invalid Source TON. Your sender '{source_addr}' needs TON=5 (Alphanumeric), but you sent TON={source_addr_ton}",
                    "sequence_number": seq,
                    "command_status": resp_status,
                    "command_status_text": "ESME_RINVSERTON (Invalid Source TON)",
                    "suggested_fix": f"Update SMSC configuration: source_addr_ton=5, source_addr_npi=0 for alphanumeric sender"
                }

            if resp_status == 0x00000000:  # Success
                return {
                    "status": "submitted",
                    "destination_number": destination_addr,
                    "message_id": msg_id,
                    "sequence_number": seq,
                    "ton_used": source_addr_ton,
                    "npi_used": source_addr_npi,
                    "registered_delivery": registered_delivery,  # Include in response for debugging
                    "dlr_requested": True  # Flag to confirm DLR was requested
                }

            return {
                "status": "failed",
                "destination_number": destination_addr,
                "error": f"SMPP Error 0x{resp_status:08X}",
                "sequence_number": seq,
                "command_status": resp_status
            }
            
        except socket.timeout:
            return {
                "status": "failed",
                "destination_number": destination_addr,
                "error": "Socket timeout waiting for response",
                "sequence_number": seq
            }
        except Exception as e:
            return {
                "status": "failed",
                "destination_number": destination_addr,
                "error": str(e),
                "sequence_number": seq
            }
        finally:
            if lock:
                try:
                    lock.release()
                except RuntimeError:
                    pass

    def submit_multi_number(self, source_addr: str, numbers: List[str], message: str, 
                           smpp_config: Dict, tlvs_map: Optional[Dict] = None) -> List[Dict]:
        """Submit SMS to multiple numbers"""
        responses = []
        for number in numbers:
            resp = self.submit_sm_with_tlvs(source_addr, number, message, smpp_config, tlvs_map)
            responses.append(resp)
            
            # Small delay between submissions to avoid flooding
            time.sleep(0.1)
            
        return responses
