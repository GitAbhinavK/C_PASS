# smpp_client.py
import socket
import struct
import binascii
import select
import logging
import re
import queue
import threading
from django.utils import timezone
from .models import SmppConnection
from .submit_sm import SMSSender
import time


logger = logging.getLogger(__name__)

class FixedSMPPClient:
    def __init__(self, smpp_connection: SmppConnection):
        self.smpp_connection = smpp_connection
        self.socket = None
        self.sequence_number = 1
        self.connected = False
        self.bound = False
        self.socket_lock = threading.RLock()
        self.incoming_pdu_queue = queue.Queue()
        # expose sender
        self.sms_sender = SMSSender(self)

    def get_sequence_number(self) -> int:
        seq = self.sequence_number
        self.sequence_number += 1
        if self.sequence_number > 0x7FFFFFFF:
            self.sequence_number = 1
        return seq

    def is_really_connected(self) -> bool:
        try:
            if not self.socket or not self.connected or not self.bound:
                return False
            self.socket.settimeout(0.1)
            try:
                self.socket.send(b'')
                return True
            except (socket.error, OSError):
                self.connected = False
                self.bound = False
                return False
        except Exception:
            return False

    def connect(self) -> bool:
        self.connected = False
        self.bound = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

        if self.smpp_connection.bind_type == 'transmitter':
            port = self.smpp_connection.tr_trx_port
        elif self.smpp_connection.bind_type == 'receiver':
            port = self.smpp_connection.rx_port
        else:
            port = self.smpp_connection.tr_trx_port or self.smpp_connection.rx_port

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(30)
            s.connect((self.smpp_connection.ip, port))
            s.settimeout(None)
            self.socket = s
            self.connected = True

            try:
                self.smpp_connection.is_alive = True
                self.smpp_connection.last_enquire_link_time = timezone.now()
                self.smpp_connection.save(update_fields=['is_alive', 'last_enquire_link_time'])
            except Exception:
                pass

            return True
        except Exception as exc:
            try:
                self.smpp_connection.last_error = str(exc)
                self.smpp_connection.is_alive = False
                self.smpp_connection.bind_status = "FAILED"
                self.smpp_connection.save(update_fields=['last_error', 'is_alive', 'bind_status'])
            except Exception:
                pass
            self.connected = False
            self.bound = False
            return False

    def ensure_connected_and_bound(self) -> bool:
        if self.is_really_connected():
            return True
        if not self.connect():
            return False
        return self.bind_connection()

    def parse_bind_resp(self, response: bytes) -> dict:
        """Parse and extract all fields from a bind response PDU."""
        result = {
            'raw_hex': binascii.hexlify(response).decode('ascii'),
            'raw_length': len(response)
        }
        
        if len(response) < 16:
            result['error'] = f"Response too short: {len(response)} bytes"
            return result
        
        # Parse header
        try:
            resp_cmd_len, resp_cmd_id, resp_status, resp_seq = struct.unpack('>IIII', response[:16])
            result['header'] = {
                'command_length': resp_cmd_len,
                'command_id': resp_cmd_id,
                'command_id_hex': f'0x{resp_cmd_id:08X}',
                'command_status': resp_status,
                'command_status_hex': f'0x{resp_status:08X}',
                'sequence_number': resp_seq
            }
            
            # Decode command ID
            command_names = {
                0x80000001: 'bind_receiver_resp',
                0x80000002: 'bind_transmitter_resp',
                0x80000009: 'bind_transceiver_resp'
            }
            result['header']['command_name'] = command_names.get(resp_cmd_id, 'unknown')
            
            # Parse body if present
            body = response[16:]
            if body:
                result['body'] = {
                    'raw': body,
                    'raw_hex': binascii.hexlify(body).decode('ascii'),
                    'system_id': None,
                    'sc_interface_version': None,
                    'addr_ton': None,
                    'addr_npi': None,
                    'address_range': None
                }
                
                # Try to parse system_id (null-terminated string)
                if b'\x00' in body:
                    system_id_end = body.find(b'\x00')
                    system_id = body[:system_id_end].decode('ascii', errors='ignore')
                    result['body']['system_id'] = system_id
                    
                    # Parse optional parameters if present
                    remaining = body[system_id_end+1:]
                    if len(remaining) >= 4:
                        try:
                            result['body']['sc_interface_version'] = struct.unpack('B', remaining[0:1])[0]
                            result['body']['addr_ton'] = struct.unpack('B', remaining[1:2])[0]
                            result['body']['addr_npi'] = struct.unpack('B', remaining[2:3])[0]
                            
                            # Address range (null-terminated string)
                            if remaining[3:] and b'\x00' in remaining[3:]:
                                addr_range_end = remaining[3:].find(b'\x00')
                                address_range = remaining[3:3+addr_range_end].decode('ascii', errors='ignore')
                                result['body']['address_range'] = address_range
                        except:
                            pass
        
        except Exception as e:
            result['parse_error'] = str(e)
        
        return result

    def print_bind_response(self, response_data: dict):
        """Print bind response in a readable format."""
        print("\n" + "="*60)
        print("SMPP BIND RESPONSE DETAILS")
        print("="*60)
        
        print(f"\nRAW RESPONSE:")
        print(f"  Length: {response_data['raw_length']} bytes")
        print(f"  Hex: {response_data['raw_hex']}")
        
        if 'error' in response_data:
            print(f"\nERROR: {response_data['error']}")
            return
        
        if 'header' in response_data:
            hdr = response_data['header']
            print(f"\nHEADER:")
            print(f"  Command Length: {hdr['command_length']}")
            print(f"  Command ID: {hdr['command_id_hex']} ({hdr['command_name']})")
            print(f"  Command Status: {hdr['command_status_hex']}")
            
            # Add status description
            smpp_errors = {
                0: "ESME_ROK (Success)",
                1: "ESME_RINVMSGLEN (Message Length is invalid)",
                2: "ESME_RINVCMDLEN (Command Length is invalid)",
                3: "ESME_RINVCMDID (Invalid Command ID)",
                4: "ESME_RINVBNDSTS (Incorrect BIND Status for given command)",
                5: "ESME_RALYBND (ESME Already in Bound State)",
                0x0D: "ESME_RINVSYSID (Invalid System ID)",
                0x0E: "ESME_RINVPASWD (Invalid Password)",
                0x0F: "ESME_RINVSYSTYP (Invalid System Type)",
            }
            status_desc = smpp_errors.get(hdr['command_status'], "Unknown status")
            print(f"  Status Description: {status_desc}")
            print(f"  Sequence Number: {hdr['sequence_number']}")
        
        if 'body' in response_data:
            body = response_data['body']
            print(f"\nBODY:")
            print(f"  System ID: {body['system_id'] or 'Not provided'}")
            if body['sc_interface_version'] is not None:
                print(f"  SC Interface Version: 0x{body['sc_interface_version']:02X}")
            if body['addr_ton'] is not None:
                print(f"  Addr TON: 0x{body['addr_ton']:02X} ({body['addr_ton']})")
            if body['addr_npi'] is not None:
                print(f"  Addr NPI: 0x{body['addr_npi']:02X} ({body['addr_npi']})")
            if body['address_range'] is not None:
                print(f"  Address Range: {body['address_range']}")
        
        if 'parse_error' in response_data:
            print(f"\nPARSE ERROR: {response_data['parse_error']}")
        
        print("\n" + "="*60 + "\n")

    def bind_connection(self) -> bool:
        if not self.connected or not self.socket:
            if not self.connect():
                try:
                    self.smpp_connection.bind_status = "FAILED"
                    self.smpp_connection.last_error = "TCP connect failed during bind"
                    self.smpp_connection.save(update_fields=['bind_status', 'last_error'])
                except Exception:
                    pass
                return False

        if self.smpp_connection.bind_type == 'transmitter':
            command_id = 0x00000002
        elif self.smpp_connection.bind_type == 'receiver':
            command_id = 0x00000001
        else:
            command_id = 0x00000009

        system_id = (self.smpp_connection.username or '').encode('ascii') + b'\x00'
        password = (self.smpp_connection.password or '').encode('ascii') + b'\x00'
        system_type = (self.smpp_connection.system_type or '').encode('ascii') + b'\x00'
        interface_version = struct.pack('B', 0x34)
        addr_ton = struct.pack('B', 0x01)
        addr_npi = struct.pack('B', 0x01)
        address_range = b'\x00'
        tail = interface_version + addr_ton + addr_npi + address_range

        command_length = 16 + len(system_id) + len(password) + len(system_type) + len(tail)
        command_status = 0x00000000
        sequence_number = self.get_sequence_number()

        try:
            # Print bind request details
            print("\n" + "="*60)
            print("SENDING SMPP BIND REQUEST")
            print("="*60)
            print(f"Command ID: 0x{command_id:08X}")
            print(f"Sequence Number: {sequence_number}")
            print(f"System ID: {self.smpp_connection.username}")
            print(f"System Type: {self.smpp_connection.system_type}")
            print("="*60 + "\n")

            pdu = struct.pack('>IIII', command_length, command_id, command_status, sequence_number)
            pdu += system_id + password + system_type + tail

            self.socket.settimeout(30)
            self.socket.send(pdu)
            response = self.socket.recv(1024)

            # Parse and print the response
            resp_data = self.parse_bind_resp(response)
            self.print_bind_response(resp_data)

            if not response or len(response) < 16:
                self.smpp_connection.last_error = "No or invalid bind response"
                self.smpp_connection.bind_status = "FAILED"
                try:
                    self.smpp_connection.save(update_fields=['last_error', 'bind_status'])
                except Exception:
                    pass
                return False

            resp_cmd_len, resp_cmd_id, resp_status, resp_seq = struct.unpack('>IIII', response[:16])
            expected_resp_id = command_id + 0x80000000
            if resp_cmd_id != expected_resp_id:
                self.smpp_connection.last_error = f"Unexpected response cmd id: {resp_cmd_id:08x}"
                self.smpp_connection.bind_status = "FAILED"
                try:
                    self.smpp_connection.save(update_fields=['last_error', 'bind_status'])
                except Exception:
                    pass
                return False

            if resp_status == 0:
                self.bound = True
                self.smpp_connection.bind_status = "BOUND"
                self.smpp_connection.last_bind_time = timezone.now()
                self.smpp_connection.is_alive = True
                self.smpp_connection.last_error = None
                try:
                    self.smpp_connection.save(update_fields=['bind_status', 'last_bind_time', 'is_alive', 'last_error'])
                except Exception:
                    pass
                return True
            else:
                smpp_errors = {
                    1: "Message Length is invalid",
                    2: "Command Length is invalid",
                    3: "Invalid Command ID",
                    4: "Incorrect BIND Status for given command",
                    5: "ESME Already in Bound State",
                    0x0D: "Invalid System ID",
                    0x0E: "Invalid Password",
                    0x0F: "Invalid System Type",
                }
                error_desc = smpp_errors.get(resp_status, f"Unknown error code: 0x{resp_status:08x}")
                self.smpp_connection.last_error = f"Bind failed: {error_desc}"
                self.smpp_connection.bind_status = "FAILED"
                try:
                    self.smpp_connection.save(update_fields=['last_error', 'bind_status'])
                except Exception:
                    pass
                return False

        except socket.timeout:
            self.smpp_connection.last_error = "Timeout waiting for bind response"
            self.smpp_connection.bind_status = "FAILED"
            try:
                self.smpp_connection.save(update_fields=['last_error', 'bind_status'])
            except Exception:
                pass
            return False
        except Exception as exc:
            self.smpp_connection.last_error = str(exc)
            self.smpp_connection.bind_status = "FAILED"
            try:
                self.smpp_connection.save(update_fields=['last_error', 'bind_status'])
            except Exception:
                pass
            return False

    def unbind(self) -> bool:
        try:
            if not self.socket or not self.connected:
                try:
                    self.smpp_connection.bind_status = "UNBOUND"
                    self.smpp_connection.is_alive = False
                    self.smpp_connection.save(update_fields=['bind_status', 'is_alive'])
                except Exception:
                    pass
                self.socket = None
                self.connected = False
                self.bound = False
                return True

            command_id = 0x00000006
            command_status = 0x00000000
            sequence_number = self.get_sequence_number()
            pdu = struct.pack('>IIII', 16, command_id, command_status, sequence_number)

            try:
                self.socket.send(pdu)
            except Exception:
                pass

            try:
                self.socket.settimeout(10)
                _ = self.socket.recv(1024)
            except Exception:
                pass

            try:
                self.socket.close()
            except Exception:
                pass

            self.socket = None
            self.connected = False
            self.bound = False

            try:
                self.smpp_connection.bind_status = "UNBOUND"
                self.smpp_connection.is_alive = False
                self.smpp_connection.last_error = None
                self.smpp_connection.save(update_fields=['bind_status', 'is_alive', 'last_error'])
            except Exception:
                pass

            return True
        except Exception as exc:
            try:
                if self.socket:
                    self.socket.close()
            except Exception:
                pass
            self.socket = None
            self.connected = False
            self.bound = False
            try:
                self.smpp_connection.bind_status = "UNBOUND"
                self.smpp_connection.is_alive = False
                self.smpp_connection.last_error = str(exc)
                self.smpp_connection.save(update_fields=['bind_status', 'is_alive', 'last_error'])
            except Exception:
                pass
            return True

    def force_close(self) -> bool:
        try:
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
            self.socket = None
            self.connected = False
            self.bound = False
            try:
                self.smpp_connection.is_alive = False
                self.smpp_connection.bind_status = "UNBOUND"
                self.smpp_connection.save(update_fields=['is_alive', 'bind_status'])
            except Exception:
                pass
            return True
        except Exception:
            return False

    def get_bind_response(self, response: bytes) -> dict:
        """
        Public method to get bind response details.
        Useful for debugging or logging outside the bind_connection method.
        
        Args:
            response: Raw bytes from bind response
        
        Returns:
            Dictionary with parsed bind response details
        """
        return self.parse_bind_resp(response)
    
def _read_pdu(self, timeout=5):
    """
    Read a PDU from the socket with timeout and proper error handling.
    Returns raw PDU bytes or None.
    """
    try:
        # Check if socket is ready
        ready, _, _ = select.select([self.socket], [], [], timeout)
        if not ready:
            return None
        
        # FIRST: Check for socket errors before reading
        try:
            # Check if socket is still alive
            self.socket.settimeout(0.1)
            test_byte = self.socket.recv(1, socket.MSG_PEEK)
            if not test_byte:
                logger.warning("⚠️ Socket appears closed (no data available)")
                return None
        except socket.timeout:
            # No data available yet, that's OK
            pass
        except Exception as e:
            logger.error(f"❌ Socket error before reading: {str(e)}")
            return None
        finally:
            self.socket.settimeout(None)
        
        # Read exactly 16 bytes for the header
        header = b''
        bytes_received = 0
        start_time = time.time()
        
        while bytes_received < 16:
            try:
                chunk = self.socket.recv(16 - bytes_received)
                if not chunk:
                    logger.warning("⚠️ Connection closed while reading PDU header")
                    return None
                header += chunk
                bytes_received += len(chunk)
                
                # Check for timeout
                if time.time() - start_time > timeout:
                    logger.warning(f"⏰ Timeout reading PDU header: {bytes_received}/16 bytes")
                    return None
            except socket.timeout:
                logger.warning("⏰ Socket timeout reading PDU header")
                return None
            except Exception as e:
                logger.error(f"❌ Error reading PDU header: {str(e)}")
                return None
        
        if len(header) < 16:
            logger.warning(f"⚠️ Incomplete PDU header: {len(header)}/16 bytes")
            return None
        
        # Log the raw header for debugging
        hex_dump = binascii.hexlify(header).decode('ascii')
        logger.debug(f"🔍 Raw header bytes: {hex_dump}")
        
        # Parse command length from header
        try:
            # Use try-except for struct.unpack to catch malformed data
            command_length = struct.unpack('>I', header[:4])[0]
        except struct.error as e:
            logger.error(f"❌ Failed to parse PDU length from header: {str(e)}")
            logger.debug(f"🔍 Corrupted header hex: {hex_dump}")
            # Try to flush any remaining garbage from socket
            try:
                self.socket.settimeout(0.1)
                self.socket.recv(4096)
            except:
                pass
            return None
        
        # Enhanced sanity checks
        # 1. Command length must be >= 16 (minimum PDU size)
        # 2. Command length must be <= 10000 (maximum reasonable PDU size)
        # 3. Check for common garbage patterns
        if command_length < 16:
            logger.error(f"⚠️ PDU length too small: {command_length} bytes")
            logger.debug(f"🔍 Header hex: {hex_dump}")
            # This is likely garbage - flush socket
            try:
                self.socket.settimeout(0.1)
                garbage = self.socket.recv(4096)
                if garbage:
                    logger.warning(f"⚠️ Flushed {len(garbage)} bytes of garbage from socket")
            except:
                pass
            return None
        
        if command_length > 10000:  # Max reasonable PDU size
            logger.error(f"⚠️ PDU length unreasonably large: {command_length} bytes (likely garbage)")
            logger.debug(f"🔍 Header hex: {hex_dump}")
            # Calculate what the bytes actually are
            try:
                # The 4 bytes that were interpreted as command_length
                first_4_bytes = header[:4]
                as_hex = binascii.hexlify(first_4_bytes).decode('ascii')
                as_int = int.from_bytes(first_4_bytes, byteorder='big', signed=False)
                logger.debug(f"🔍 First 4 bytes: hex={as_hex}, int={as_int}")
                
                # Check if these look like ASCII characters
                try:
                    as_text = first_4_bytes.decode('ascii', errors='ignore')
                    logger.debug(f"🔍 As text: '{as_text}'")
                except:
                    pass
            except:
                pass
            
            # Flush potential garbage
            try:
                self.socket.settimeout(0.1)
                flushed = 0
                while True:
                    chunk = self.socket.recv(1024)
                    if not chunk:
                        break
                    flushed += len(chunk)
                    if flushed > 4096:  # Don't read forever
                        break
                if flushed > 0:
                    logger.warning(f"⚠️ Flushed {flushed} bytes after detecting garbage PDU")
            except:
                pass
            
            return None
        
        # Read remaining PDU body
        remaining = command_length - 16
        if remaining > 0:
            body = b''
            bytes_received = 0
            
            while bytes_received < remaining:
                try:
                    chunk_size = min(4096, remaining - bytes_received)
                    chunk = self.socket.recv(chunk_size)
                    if not chunk:
                        logger.warning(f"⚠️ Connection closed while reading PDU body")
                        return None
                    body += chunk
                    bytes_received += len(chunk)
                    
                    # Check for timeout
                    if time.time() - start_time > timeout:
                        logger.warning(f"⏰ Timeout reading PDU body: {bytes_received}/{remaining} bytes")
                        return None
                except socket.timeout:
                    logger.warning("⏰ Socket timeout reading PDU body")
                    return None
                except Exception as e:
                    logger.error(f"❌ Error reading PDU body: {str(e)}")
                    return None
            
            if bytes_received < remaining:
                logger.warning(f"⚠️ Incomplete PDU body: {bytes_received}/{remaining} bytes")
                return None
            
            full_pdu = header + body
        else:
            full_pdu = header
        
        # Verify PDU length matches what we parsed
        if len(full_pdu) != command_length:
            logger.error(f"⚠️ PDU length mismatch: parsed={command_length}, actual={len(full_pdu)}")
            return None
        
        # Log successful PDU read
        logger.debug(f"📥 Successfully read PDU: {command_length} bytes, cmd_id=0x{struct.unpack('>I', header[4:8])[0]:08X}")
        
        return full_pdu
        
    except socket.timeout:
        logger.debug("⏰ Overall timeout reading PDU")
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected error in _read_pdu: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None
      
def read_pdu_with_parsing(self, timeout=5):
    """
    Read and parse a PDU from the socket.
    Returns a dictionary with parsed PDU data or None if garbage/error.
    """
    raw_pdu = self._read_pdu(timeout)
    if not raw_pdu:
        # Special case: If we got None because of garbage, we might want to
        # retry immediately if the socket is still good
        if self.socket and self.connected:
            # Check if socket still seems alive
            try:
                self.socket.settimeout(0.1)
                test_byte = self.socket.recv(1, socket.MSG_PEEK)
                if test_byte:
                    # There's still data - might be another PDU
                    logger.debug("🔄 Socket still has data after garbage, trying again...")
                    raw_pdu = self._read_pdu(timeout)
            except:
                pass
            finally:
                self.socket.settimeout(None)
        
        if not raw_pdu:
            return None
    def _extract_short_message_from_deliver_sm(self, raw_pdu: bytes) -> str:
        """
        Extract short_message from a deliver_sm PDU.
        Simplified approach: Look for printable text at the end of the PDU.
        """
        try:
            if len(raw_pdu) < 50:  # Minimum reasonable deliver_sm size
                return ""
            
            # Parse command length from header
            command_length = struct.unpack('>I', raw_pdu[:4])[0]
            
            # Ensure we have the full PDU
            if len(raw_pdu) < command_length:
                logger.warning(f"⚠️ Incomplete deliver_sm PDU: {len(raw_pdu)}/{command_length} bytes")
                return ""
            
            # Try to find the message part (last 100 bytes or so)
            search_start = max(0, len(raw_pdu) - 100)
            message_part = raw_pdu[search_start:]
            
            # Try to decode as ASCII or UTF-8
            try:
                # First try ASCII
                text = message_part.decode('ascii', errors='ignore')
                if text and any(c.isalnum() for c in text):
                    return text.strip()
            except:
                pass
            
            # Try Latin-1
            try:
                text = message_part.decode('latin1', errors='ignore')
                if text and any(c.isalnum() for c in text):
                    return text.strip()
            except:
                pass
            
            # Try to find common DLR patterns
            patterns = [
                b'id:', b'stat:', b'sub:', b'dlvrd:', b'submit date:', b'done date:', b'err:'
            ]
            
            for pattern in patterns:
                if pattern in raw_pdu:
                    # Found a DLR pattern, extract around it
                    pattern_pos = raw_pdu.find(pattern)
                    start = max(0, pattern_pos - 10)
                    end = min(len(raw_pdu), pattern_pos + 100)
                    text = raw_pdu[start:end].decode('ascii', errors='ignore')
                    return text
            
            # Last resort: Try to decode the entire PDU from position 16
            try:
                body = raw_pdu[16:]
                text = body.decode('ascii', errors='ignore')
                # Clean up null bytes
                text = text.replace('\x00', ' ')
                if len(text.strip()) > 10:
                    return text.strip()[:200]
            except:
                pass
            
            return ""
            
        except Exception as e:
            logger.error(f"❌ Error extracting short message: {str(e)}")
            return ""
    
    def send_enquire_link(self):
        """Send enquire_link PDU to keep connection alive."""
        try:
            if not self.socket or not self.connected or not self.bound:
                logger.warning("⚠️ Cannot send enquire_link: Not connected or bound")
                return False
            
            command_id = 0x00000015  # enquire_link
            command_status = 0x00000000
            sequence_number = self.get_sequence_number()
            
            # Create PDU: command_length (16), command_id, command_status, sequence_number
            pdu = struct.pack('>IIII', 16, command_id, command_status, sequence_number)
            
            self.socket.send(pdu)
            
            # Try to get response
            response = self._read_pdu(timeout=5)
            if response:
                try:
                    resp_len, resp_cmd_id, resp_status, resp_seq = struct.unpack('>IIII', response[:16])
                    if resp_cmd_id == 0x80000015:  # enquire_link_resp
                        try:
                            self.smpp_connection.last_enquire_link_time = timezone.now()
                            self.smpp_connection.save(update_fields=['last_enquire_link_time'])
                        except:
                            pass
                        logger.debug(f"✅ Enquire_link sent and acknowledged, sequence: {sequence_number}")
                        return True
                except:
                    pass
                
            logger.warning("⚠️ No valid response to enquire_link")
            return False
            
        except socket.timeout:
            logger.warning("⏰ Timeout sending enquire_link")
            return False
        except Exception as e:
            logger.error(f"❌ Error sending enquire_link: {str(e)}")
            return False
