# smpp_dlr_manager.py - COMPLETE FIXED VERSION with proper database updates
import threading
import time
import logging
import json
import re
import struct
import socket
import binascii
import select
import queue
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from .models import ComposeMessageLine, ComposeMessage, Credit

logger = logging.getLogger(__name__)

# Map SMPP stat codes to human-readable status
SMPP_STATUS_MAP = {
    "DELIVRD": "DELIVERED",
    "UNDELIV": "UNDELIVERED",
    "REJECTD": "REJECTED",
    "EXPIRED": "EXPIRED",
    "DELETED": "DELETED",
    "ACCEPTD": "ACCEPTED",
    "UNKNOWN": "UNKNOWN",
    "ENROUTE": "EN ROUTE"
}


class DLRManager:
    """Singleton manager for all DLR listeners"""
    
    _instance = None
    _listeners = {}  # smpp_id -> DLRListener
    _lock = threading.Lock()
    _session_map = {}  # Track which sessions have active listeners
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DLRManager, cls).__new__(cls)
            cls._instance._listeners = {}
            cls._instance._session_map = {}
        return cls._instance
    
    def __init__(self):
        # Ensure listeners dict exists
        if not hasattr(self, '_listeners'):
            self._listeners = {}
        if not hasattr(self, '_session_map'):
            self._session_map = {}
    
    @classmethod
    def start_listener(cls, session):
        """Start DLR listener for a session"""
        instance = cls()
        
        with cls._lock:
            # Get session identifier
            session_id = id(session)  # Unique session object ID
            smpp_id = getattr(session.smpp_connection, "smpp_id", None)
            
            if not smpp_id:
                logger.error("❌ Cannot start DLR listener: No smpp_id in session")
                return None
            
            # Check if this exact session already has a listener
            if session_id in instance._session_map:
                existing_smpp_id = instance._session_map[session_id]
                if existing_smpp_id in instance._listeners:
                    listener = instance._listeners[existing_smpp_id]
                    if listener and listener.is_alive():
                        logger.info(f"✅ DLR listener already running for session {session_id} (SMPP ID: {smpp_id})")
                        return listener
                    else:
                        # Remove dead listener
                        logger.warning(f"⚠️ Removing dead listener for SMPP ID: {existing_smpp_id}")
                        if existing_smpp_id in instance._listeners:
                            del instance._listeners[existing_smpp_id]
                        del instance._session_map[session_id]
            
            # Check if there's already a listener for this SMPP ID (different session)
            if smpp_id in instance._listeners:
                listener = instance._listeners[smpp_id]
                if listener and listener.is_alive():
                    logger.warning(f"⚠️ Another DLR listener already running for SMPP ID: {smpp_id}")
                    logger.warning(f"⚠️ Stopping old listener and starting new one for session {session_id}")
                    listener.stop()
                    time.sleep(0.5)  # Give it a moment to stop
            
            # Create and start new listener
            try:
                listener = DLRListener(session)
                instance._listeners[smpp_id] = listener
                instance._session_map[session_id] = smpp_id
                listener.start()
                time.sleep(0.1)  # Small delay to ensure thread starts
                logger.info(f"🚀 Started DLR listener for SMPP ID: {smpp_id} (session: {session_id})")
                return listener
            except Exception as e:
                logger.error(f"❌ Failed to start DLR listener: {str(e)}")
                return None
    
    @classmethod
    def stop_listener(cls, smpp_id):
        """Stop DLR listener for a session"""
        instance = cls()
        with cls._lock:
            if smpp_id in instance._listeners:
                listener = instance._listeners[smpp_id]
                listener.stop()
                del instance._listeners[smpp_id]
                
                # Remove from session map
                session_ids_to_remove = []
                for session_id, map_smpp_id in instance._session_map.items():
                    if map_smpp_id == smpp_id:
                        session_ids_to_remove.append(session_id)
                
                for session_id in session_ids_to_remove:
                    del instance._session_map[session_id]
                
                logger.info(f"🛑 Stopped DLR listener for SMPP ID: {smpp_id}")
    
    @classmethod
    def stop_all_listeners(cls):
        """Stop all DLR listeners"""
        instance = cls()
        with cls._lock:
            for smpp_id, listener in list(instance._listeners.items()):
                try:
                    listener.stop()
                    logger.info(f"🛑 Stopped DLR listener for SMPP ID: {smpp_id}")
                except Exception as e:
                    logger.error(f"❌ Error stopping listener for {smpp_id}: {str(e)}")
            
            instance._listeners.clear()
            instance._session_map.clear()
            logger.info("🛑 All DLR listeners stopped")
    
    @classmethod
    def get_listener(cls, smpp_id):
        """Get DLR listener for a session"""
        instance = cls()
        with cls._lock:
            listener = instance._listeners.get(smpp_id)
            if listener and listener.is_alive():
                return listener
            elif listener and not listener.is_alive():
                # Clean up dead listener
                del instance._listeners[smpp_id]
                return None
            return None
    
    @classmethod
    def get_status(cls):
        """Get status of all DLR listeners"""
        instance = cls()
        with cls._lock:
            status = []
            for smpp_id, listener in instance._listeners.items():
                is_alive = listener.is_alive() if hasattr(listener, 'is_alive') else False
                # Get listener name safely
                listener_name = getattr(listener, 'smpp_name', f"SMPP-{smpp_id}")
                
                status.append({
                    'smpp_id': smpp_id,
                    'smpp_name': listener_name,
                    'running': is_alive,
                    'alive': is_alive,
                    'session_count': sum(1 for sid, mid in instance._session_map.items() if mid == smpp_id),
                    'messages_processed': getattr(listener, 'message_count', 0),
                    'last_activity': time.strftime("%H:%M:%S", time.localtime(getattr(listener, 'last_activity', 0))),
                    'thread_name': listener.name if hasattr(listener, 'name') else 'Unknown',
                    'errors': getattr(listener, 'error_count', 0)
                })
            return status
    
    @classmethod
    def get_all_listeners(cls):
        """Get all active listeners"""
        instance = cls()
        with cls._lock:
            # Return only alive listeners
            alive_listeners = {}
            for smpp_id, listener in instance._listeners.items():
                if listener and listener.is_alive():
                    alive_listeners[smpp_id] = listener
                else:
                    # Clean up dead listener
                    try:
                        del instance._listeners[smpp_id]
                    except KeyError:
                        pass
            return alive_listeners
    
    @classmethod
    def cleanup_dead_listeners(cls):
        """Clean up dead listeners"""
        instance = cls()
        with cls._lock:
            dead_smpp_ids = []
            for smpp_id, listener in instance._listeners.items():
                if not listener.is_alive():
                    dead_smpp_ids.append(smpp_id)
            
            for smpp_id in dead_smpp_ids:
                try:
                    del instance._listeners[smpp_id]
                    logger.info(f"🧹 Cleaned up dead listener for SMPP ID: {smpp_id}")
                except KeyError:
                    pass
            
            # Clean up session map
            alive_smpp_ids = set(instance._listeners.keys())
            session_ids_to_remove = []
            for session_id, smpp_id in instance._session_map.items():
                if smpp_id not in alive_smpp_ids:
                    session_ids_to_remove.append(session_id)
            
            for session_id in session_ids_to_remove:
                del instance._session_map[session_id]


class DLRListener(threading.Thread):
    """
    SMPP DLR listener with PROPER DATABASE UPDATES for ComposeMessageLine
    """
    
    def __init__(self, session):
        super().__init__(daemon=True, name=f"DLRListener-{id(session)}")
        self.session = session
        self.smpp_id = session.smpp_connection.smpp_id if hasattr(session.smpp_connection, 'smpp_id') else 'unknown'
        self.smpp_name = getattr(session.smpp_connection, 'connect_name', f"SMPP-{self.smpp_id}")
        self.running = True
        self.message_count = 0
        self.error_count = 0
        self.last_activity = time.time()
        self.session_id = id(session)
        
        # Connection health tracking
        self.consecutive_errors = 0
        self.max_consecutive_errors = 10
        
        # Debug counters
        self.loop_count = 0
        self.empty_reads = 0
        
        # CRITICAL: Set socket timeout if available
        if hasattr(session, 'socket') and session.socket:
            try:
                session.socket.settimeout(2.0)
                logger.info(f"✅ Socket timeout set to 2.0 seconds for listener {self.smpp_id}")
            except Exception as e:
                logger.warning(f"⚠️ Could not set socket timeout: {e}")
        
    def stop(self):
        """Gracefully stop the listener"""
        self.running = False
        logger.info(f"🛑 Stopping DLR listener for session {self.session_id} (SMPP ID: {self.smpp_id})")
    
    def is_alive(self):
        """Check if thread is alive and session is still valid"""
        try:
            thread_alive = super().is_alive()
            
            # Additional check for session validity
            if thread_alive and hasattr(self.session, 'connected'):
                return self.session.connected and self.session.bound
            return thread_alive
        except Exception:
            return False
    
    def _debug_print_hex(self, data, label="DATA"):
        """Print data in hex format for debugging"""
        if data:
            hex_str = binascii.hexlify(data).decode()
            # Print in chunks of 32 chars for readability
            chunks = [hex_str[i:i+32] for i in range(0, len(hex_str), 32)]
            logger.info(f"🔍 {label} HEX:")
            for i, chunk in enumerate(chunks):
                logger.info(f"   [{i}] {chunk}")

    def _command_name(self, command_id):
        cmd_names = {
            0x00000001: "bind_receiver",
            0x80000001: "bind_receiver_resp",
            0x00000002: "bind_transmitter",
            0x80000002: "bind_transmitter_resp",
            0x00000003: "bind_transceiver",
            0x80000003: "bind_transceiver_resp",
            0x00000004: "submit_sm",
            0x80000004: "submit_sm_resp",
            0x00000005: "deliver_sm",
            0x80000005: "deliver_sm_resp",
            0x00000006: "unbind",
            0x80000006: "unbind_resp",
            0x00000015: "enquire_link",
            0x80000015: "enquire_link_resp",
        }
        return cmd_names.get(command_id, f"UNKNOWN(0x{command_id:08x})")

    def _handle_pdu(self, pdu_data):
        command_name = pdu_data.get('command_name') or self._command_name(pdu_data.get('command_id'))
        command_id = pdu_data.get('command_id')
        sequence = pdu_data.get('sequence_number', 0)

        logger.info(f"📨 RECEIVED: {command_name} (0x{command_id:08x}) seq:{sequence}")

        if command_name == 'deliver_sm' or command_id == 0x00000005:
            logger.info(f"📬📬📬 DELIVER_SM RECEIVED! (seq: {sequence})")
            if not pdu_data.get('short_message'):
                pdu_data = self._parse_deliver_sm_fields(pdu_data)
            self._process_deliver_sm(pdu_data)
        elif command_name == 'enquire_link' or command_id == 0x00000015:
            logger.info(f"❤️ Enquire_link received (seq: {sequence}) - Sending response")
            self._send_enquire_link_resp(sequence)
        elif command_name == 'enquire_link_resp' or command_id == 0x80000015:
            logger.debug(f"❤️ Enquire_link response received for {self.smpp_name}")
        else:
            logger.info(f"📨 Received {command_name} (0x{command_id:08x}) for SMPP {self.smpp_id}")

    def _parse_deliver_sm_fields(self, pdu_data):
        """Populate deliver_sm fields for PDUs queued by the submitter."""
        body = pdu_data.get('body') or b''
        try:
            offset = 0
            while offset < len(body) and body[offset:offset+1] != b'\x00':
                offset += 1
            offset += 1

            offset += 2  # source TON/NPI
            source_addr = b''
            while offset < len(body) and body[offset:offset+1] != b'\x00':
                source_addr += body[offset:offset+1]
                offset += 1
            offset += 1

            offset += 2  # destination TON/NPI
            destination_addr = b''
            while offset < len(body) and body[offset:offset+1] != b'\x00':
                destination_addr += body[offset:offset+1]
                offset += 1
            offset += 1

            if offset + 3 > len(body):
                return pdu_data
            esm_class = body[offset]
            offset += 3  # esm_class, protocol_id, priority_flag

            while offset < len(body) and body[offset:offset+1] != b'\x00':
                offset += 1
            offset += 1
            while offset < len(body) and body[offset:offset+1] != b'\x00':
                offset += 1
            offset += 1

            offset += 2  # registered_delivery, replace_if_present_flag
            data_coding = body[offset] if offset < len(body) else 0
            offset += 1  # data_coding
            offset += 1  # sm_default_msg_id
            sm_length = body[offset] if offset < len(body) else 0
            offset += 1

            short_message_bytes = body[offset:offset + sm_length]
            if data_coding == 0x08:
                short_message = short_message_bytes.decode('utf-16-be', errors='ignore')
            else:
                short_message = short_message_bytes.decode('ascii', errors='ignore')

            pdu_data.update({
                'source_addr': source_addr.decode('ascii', errors='ignore'),
                'destination_addr': destination_addr.decode('ascii', errors='ignore'),
                'esm_class': esm_class,
                'data_coding': data_coding,
                'short_message': short_message,
            })
        except Exception as e:
            logger.error(f"❌ Error parsing queued deliver_sm: {e}")
        return pdu_data
    
    def run(self):
        """Main listener loop with enhanced debugging"""
        logger.info(f"🚀 DLR Listener started for {self.smpp_name} (ID: {self.smpp_id}, Session: {self.session_id})")
        
        # Print all available methods on session for debugging
        methods = [m for m in dir(self.session) if not m.startswith('_') and callable(getattr(self.session, m))]
        logger.info(f"🔍 Session methods ({len(methods)}): {methods[:10]}...")
        
        # Check if socket exists
        if hasattr(self.session, 'socket'):
            logger.info(f"🔌 Socket exists: {self.session.socket is not None}")
            if self.session.socket:
                logger.info(f"   Socket fileno: {self.session.socket.fileno()}")
                logger.info(f"   Socket timeout: {self.session.socket.gettimeout()}")
        else:
            logger.error(f"❌ Session has no socket attribute!")
        
        # Reset error counter
        self.consecutive_errors = 0
        last_heartbeat = time.time()
        last_enquire = time.time()
        
        while self.running:
            self.loop_count += 1
            
            try:
                # Heartbeat every 30 seconds
                if time.time() - last_heartbeat > 30:
                    logger.info(f"💓 DLR Listener active for {self.smpp_name} - Loop: {self.loop_count}, Processed: {self.message_count} DLRs, Errors: {self.error_count}, Empty reads: {self.empty_reads}")
                    last_heartbeat = time.time()
                
                # Send enquire_link every 55 seconds to keep connection alive
                if time.time() - last_enquire > 55:
                    logger.info(f"📤 Sending enquire_link to {self.smpp_name} (keep-alive)")
                    self._send_enquire_link()
                    last_enquire = time.time()
                
                # Check if session is still connected
                if not hasattr(self.session, 'bound') or not hasattr(self.session, 'connected'):
                    logger.error(f"❌ Session {self.smpp_id} has no bound/connected attributes")
                    self.running = False
                    break
                
                if not self.session.bound or not self.session.connected:
                    logger.warning(f"⚠️ Session {self.smpp_id} disconnected, stopping DLR listener")
                    self.running = False
                    break

                queued_pdu = None
                incoming_queue = getattr(self.session, "incoming_pdu_queue", None)
                if incoming_queue is not None:
                    try:
                        queued_pdu = incoming_queue.get_nowait()
                    except queue.Empty:
                        queued_pdu = None

                if queued_pdu:
                    queued_pdu.setdefault('command_length', 16 + len(queued_pdu.get('body') or b''))
                    queued_pdu.setdefault('command_name', self._command_name(queued_pdu.get('command_id')))
                    self._handle_pdu(queued_pdu)
                    continue
                
                # CHECK FOR INCOMING DATA USING SELECT
                if hasattr(self.session, 'socket') and self.session.socket:
                    try:
                        # Use select to check if data is available without blocking
                        ready_to_read, _, _ = select.select([self.session.socket], [], [], 0.5)
                        if ready_to_read:
                            logger.info(f"📥 Data available on socket for {self.smpp_name} - WILL READ NOW")
                            
                            # Try to peek at the header to see what's coming
                            try:
                                # Peek at first 16 bytes (header)
                                header = self.session.socket.recv(16, socket.MSG_PEEK)
                                if len(header) == 0:
                                    logger.warning(f"⚠️ Remote peer closed socket for {self.smpp_name}; stopping DLR listener")
                                    self.running = False
                                    if hasattr(self.session, 'connected'):
                                        self.session.connected = False
                                    if hasattr(self.session, 'bound'):
                                        self.session.bound = False
                                    break
                                if len(header) == 16:
                                    length, cmd_id, status, seq = struct.unpack(">IIII", header)
                                    
                                    # Get command name
                                    cmd_names = {
                                        0x00000001: "bind_receiver",
                                        0x80000001: "bind_receiver_resp",
                                        0x00000002: "bind_transmitter",
                                        0x80000002: "bind_transmitter_resp",
                                        0x00000003: "bind_transceiver",
                                        0x80000003: "bind_transceiver_resp",
                                        0x00000004: "submit_sm",
                                        0x80000004: "submit_sm_resp",
                                        0x00000005: "deliver_sm",
                                        0x80000005: "deliver_sm_resp",
                                        0x00000006: "unbind",
                                        0x80000006: "unbind_resp",
                                        0x00000007: "replace_sm",
                                        0x80000007: "replace_sm_resp",
                                        0x00000008: "query_sm",
                                        0x80000008: "query_sm_resp",
                                        0x00000009: "cancel_sm",
                                        0x80000009: "cancel_sm_resp",
                                        0x00000015: "enquire_link",
                                        0x80000015: "enquire_link_resp",
                                        0x00000021: "submit_multi",
                                        0x80000021: "submit_multi_resp",
                                        0x00000100: "alert_notification",
                                    }
                                    cmd_name = cmd_names.get(cmd_id, f"unknown(0x{cmd_id:08x})")
                                    
                                    logger.info(f"🔍 PEEK - Length: {length}, Command: {cmd_name} (0x{cmd_id:08x}), Status: 0x{status:08x}, Seq: {seq}")
                                    
                                    # If it's deliver_sm, log specially
                                    if cmd_id == 0x00000005:
                                        logger.info(f"🎯🎯🎯 DELIVER_SM DETECTED IN PEEK! Sequence: {seq}")
                            except Exception as e:
                                logger.error(f"❌ Error peeking socket: {e}")
                            
                            lock = getattr(self.session, "socket_lock", None)
                            lock_acquired = False
                            if lock:
                                lock_acquired = lock.acquire(blocking=False)
                                if not lock_acquired:
                                    continue

                            try:
                                # Now actually read the PDU
                                pdu_data = self._read_raw_pdu()
                            finally:
                                if lock and lock_acquired:
                                    lock.release()
                            if pdu_data:
                                self.last_activity = time.time()
                                self.consecutive_errors = 0
                                self._handle_pdu(pdu_data)
                        else:
                            # No data available, just continue
                            self.empty_reads += 1
                            if self.empty_reads % 100 == 0:  # Log every 100 empty reads
                                logger.debug(f"⏳ No data available (empty reads: {self.empty_reads})")
                    except Exception as e:
                        logger.error(f"❌ Socket select/read error: {e}")
                        self.error_count += 1
                else:
                    logger.error(f"❌ No socket available for {self.smpp_name}")
                    time.sleep(1)
                
                # Small sleep to prevent CPU spinning
                time.sleep(0.1)
                
            except socket.timeout:
                # Timeout is normal, just continue
                continue
            except Exception as e:
                self.error_count += 1
                self.consecutive_errors += 1
                
                logger.error(f"❌ DLR Listener error #{self.error_count} for SMPP {self.smpp_id}: {str(e)}")
                import traceback
                traceback.print_exc()
                
                if self.consecutive_errors > self.max_consecutive_errors:
                    logger.error(f"❌ Too many consecutive errors ({self.consecutive_errors}), stopping DLR listener")
                    self.running = False
                    break
                
                # Longer sleep on error to avoid tight loop
                time.sleep(2)
        
        logger.info(f"🛑 DLR Listener stopped for {self.smpp_name} (ID: {self.smpp_id})")
    
    def _read_raw_pdu(self):
        """Read a raw PDU from socket and parse it"""
        try:
            if not hasattr(self.session, 'socket') or not self.session.socket:
                logger.error("❌ No socket available for reading")
                return None
            
            # Read header (16 bytes)
            header = self.session.socket.recv(16)
            if len(header) == 0:
                logger.warning(f"⚠️ Socket closed while reading header for {self.smpp_name}")
                self.running = False
                if hasattr(self.session, 'connected'):
                    self.session.connected = False
                if hasattr(self.session, 'bound'):
                    self.session.bound = False
                return None
            if len(header) < 16:
                logger.warning(f"⚠️ Incomplete header received: {len(header)} bytes")
                return None
            
            # Parse header
            length, command_id, command_status, sequence_number = struct.unpack(">IIII", header)
            
            logger.info(f"📥 RAW PDU - Len:{length}, Cmd:0x{command_id:08x}, Status:0x{command_status:08x}, Seq:{sequence_number}")
            
            # Read body
            body_length = length - 16
            body = b''
            if body_length > 0:
                body = self.session.socket.recv(body_length)
                if len(body) < body_length:
                    logger.warning(f"⚠️ Incomplete body received: {len(body)}/{body_length} bytes")
            
            # Log raw hex for debugging
            self._debug_print_hex(header + body, "FULL PDU")
            
            # Parse into dictionary
            pdu_data = {
                'command_length': length,
                'command_id': command_id,
                'command_status': command_status,
                'sequence_number': sequence_number,
                'body': body
            }
            
            # Add command name
            cmd_names = {
                0x00000001: "bind_receiver",
                0x80000001: "bind_receiver_resp",
                0x00000002: "bind_transmitter",
                0x80000002: "bind_transmitter_resp",
                0x00000003: "bind_transceiver",
                0x80000003: "bind_transceiver_resp",
                0x00000004: "submit_sm",
                0x80000004: "submit_sm_resp",
                0x00000005: "deliver_sm",
                0x80000005: "deliver_sm_resp",
                0x00000006: "unbind",
                0x80000006: "unbind_resp",
                0x00000015: "enquire_link",
                0x80000015: "enquire_link_resp",
            }
            pdu_data['command_name'] = cmd_names.get(command_id, f"UNKNOWN(0x{command_id:08x})")
            
            # Parse basic fields for deliver_sm
            if command_id == 0x00000005:  # deliver_sm
                try:
                    # Simple parsing of deliver_sm
                    offset = 0
                    # service_type (null terminated)
                    service_type = b''
                    while offset < len(body) and body[offset:offset+1] != b'\x00':
                        service_type += body[offset:offset+1]
                        offset += 1
                    offset += 1  # Skip null
                    
                    # source_addr_ton, source_addr_npi
                    if offset + 2 <= len(body):
                        source_addr_ton, source_addr_npi = struct.unpack(">BB", body[offset:offset+2])
                        offset += 2
                    
                    # source_addr (null terminated)
                    source_addr = b''
                    while offset < len(body) and body[offset:offset+1] != b'\x00':
                        source_addr += body[offset:offset+1]
                        offset += 1
                    offset += 1
                    
                    # dest_addr_ton, dest_addr_npi
                    if offset + 2 <= len(body):
                        dest_addr_ton, dest_addr_npi = struct.unpack(">BB", body[offset:offset+2])
                        offset += 2
                    
                    # destination_addr (null terminated)
                    destination_addr = b''
                    while offset < len(body) and body[offset:offset+1] != b'\x00':
                        destination_addr += body[offset:offset+1]
                        offset += 1
                    offset += 1
                    
                    # esm_class
                    if offset < len(body):
                        esm_class = body[offset]
                        offset += 1
                    else:
                        esm_class = 0
                    
                    # protocol_id
                    if offset < len(body):
                        protocol_id = body[offset]
                        offset += 1
                    else:
                        protocol_id = 0
                    
                    # priority_flag
                    if offset < len(body):
                        priority_flag = body[offset]
                        offset += 1
                    else:
                        priority_flag = 0
                    
                    # schedule_delivery_time (null terminated)
                    while offset < len(body) and body[offset:offset+1] != b'\x00':
                        offset += 1
                    offset += 1
                    
                    # validity_period (null terminated)
                    while offset < len(body) and body[offset:offset+1] != b'\x00':
                        offset += 1
                    offset += 1
                    
                    # registered_delivery
                    if offset < len(body):
                        registered_delivery = body[offset]
                        offset += 1
                    else:
                        registered_delivery = 0
                    
                    # replace_if_present_flag
                    if offset < len(body):
                        replace_if_present_flag = body[offset]
                        offset += 1
                    else:
                        replace_if_present_flag = 0
                    
                    # data_coding
                    if offset < len(body):
                        data_coding = body[offset]
                        offset += 1
                    else:
                        data_coding = 0
                    
                    # sm_default_msg_id
                    if offset < len(body):
                        sm_default_msg_id = body[offset]
                        offset += 1
                    else:
                        sm_default_msg_id = 0
                    
                    # sm_length
                    if offset < len(body):
                        sm_length = body[offset]
                        offset += 1
                    else:
                        sm_length = 0
                    
                    # short_message
                    if offset + sm_length <= len(body):
                        short_message_bytes = body[offset:offset+sm_length]
                        try:
                            if data_coding == 0x08:  # UCS-2
                                short_message = short_message_bytes.decode('utf-16-be')
                            else:
                                short_message = short_message_bytes.decode('ascii', errors='ignore')
                        except:
                            short_message = short_message_bytes.decode('ascii', errors='ignore')
                    else:
                        short_message = ""
                    
                    pdu_data.update({
                        'source_addr': source_addr.decode('ascii', errors='ignore'),
                        'destination_addr': destination_addr.decode('ascii', errors='ignore'),
                        'esm_class': esm_class,
                        'data_coding': data_coding,
                        'short_message': short_message,
                    })
                    
                    logger.info(f"📬 DELIVER_SM parsed - From: {pdu_data['source_addr']}, To: {pdu_data['destination_addr']}, ESM: 0x{esm_class:02x}")
                    
                except Exception as e:
                    logger.error(f"❌ Error parsing deliver_sm: {e}")
            
            return pdu_data
            
        except socket.timeout:
            return None
        except Exception as e:
            logger.error(f"❌ Error reading raw PDU: {e}")
            return None
    
    def _send_enquire_link(self):
        """Send enquire_link to keep connection alive"""
        try:
            if hasattr(self.session, 'send_pdu'):
                seq = 1
                if hasattr(self.session, 'get_sequence_number'):
                    seq = self.session.get_sequence_number()
                
                enquire_pdu = {
                    'command_id': 0x00000015,  # enquire_link
                    'command_status': 0,
                    'sequence_number': seq,
                    'body': {}
                }
                self.session.send_pdu(enquire_pdu)
                logger.info(f"📤 Sent enquire_link (seq: {seq}) to {self.smpp_name}")
                return True
            else:
                # Try direct socket send
                if hasattr(self.session, 'socket') and self.session.socket:
                    lock = getattr(self.session, "socket_lock", None)
                    if lock:
                        lock.acquire()
                    try:
                        seq = 1
                        if hasattr(self.session, 'get_sequence_number'):
                            seq = self.session.get_sequence_number()
                        
                        # Build enquire_link PDU manually
                        length = 16  # header only
                        cmd_id = 0x00000015
                        status = 0
                        pdu = struct.pack(">IIII", length, cmd_id, status, seq)
                        self.session.socket.sendall(pdu)
                    finally:
                        if lock:
                            lock.release()
                    logger.info(f"📤 Sent enquire_link via raw socket (seq: {seq}) to {self.smpp_name}")
                    return True
                    
                logger.error(f"❌ Cannot send enquire_link - no method available")
                return False
        except Exception as e:
            logger.error(f"❌ Error sending enquire_link: {str(e)}")
            return False
    
    def _send_enquire_link_resp(self, sequence_number):
        """Send enquire_link_resp to keep connection alive"""
        try:
            if hasattr(self.session, 'send_pdu'):
                resp_pdu = {
                    'command_id': 0x80000015,  # enquire_link_resp
                    'command_status': 0,
                    'sequence_number': sequence_number,
                    'body': {}
                }
                self.session.send_pdu(resp_pdu)
                logger.info(f"📤 Sent enquire_link_resp (seq: {sequence_number}) to {self.smpp_name}")
                return True
            else:
                # Try direct socket send
                if hasattr(self.session, 'socket') and self.session.socket:
                    lock = getattr(self.session, "socket_lock", None)
                    if lock:
                        lock.acquire()
                    try:
                        length = 16  # header only
                        cmd_id = 0x80000015
                        status = 0
                        pdu = struct.pack(">IIII", length, cmd_id, status, sequence_number)
                        self.session.socket.sendall(pdu)
                    finally:
                        if lock:
                            lock.release()
                    logger.info(f"📤 Sent enquire_link_resp via raw socket (seq: {sequence_number}) to {self.smpp_name}")
                    return True
                    
                logger.error(f"❌ Cannot send enquire_link_resp - no method available")
                return False
        except Exception as e:
            logger.error(f"❌ Error sending enquire_link_resp: {str(e)}")
            return False
    
    def _send_deliver_sm_resp(self, sequence_number, command_status=0):
        """CRITICAL: Send deliver_sm_resp to acknowledge DLR receipt"""
        try:
            logger.info(f"📤 Sending deliver_sm_resp (seq: {sequence_number}) to {self.smpp_name}")
            
            if hasattr(self.session, 'send_deliver_sm_resp'):
                result = self.session.send_deliver_sm_resp(sequence_number, command_status)
                if result:
                    logger.info(f"✅ deliver_sm_resp sent (seq: {sequence_number}) to {self.smpp_name}")
                    return True
            elif hasattr(self.session, 'send_pdu'):
                # Manual construction if method doesn't exist
                resp_pdu = {
                    'command_id': 0x80000005,  # deliver_sm_resp
                    'command_status': command_status,
                    'sequence_number': sequence_number,
                    'body': {'message_id': ''}
                }
                self.session.send_pdu(resp_pdu)
                logger.info(f"✅ deliver_sm_resp sent via send_pdu (seq: {sequence_number}) to {self.smpp_name}")
                return True
            else:
                # Try direct socket send
                if hasattr(self.session, 'socket') and self.session.socket:
                    lock = getattr(self.session, "socket_lock", None)
                    if lock:
                        lock.acquire()
                    try:
                        # Build minimal deliver_sm_resp
                        # Header (16 bytes) + message_id field (null terminated)
                        message_id = b'\x00'  # empty message_id
                        body_length = len(message_id)
                        total_length = 16 + body_length
                        
                        pdu = struct.pack(">IIII", total_length, 0x80000005, command_status, sequence_number)
                        pdu += message_id
                        
                        self.session.socket.sendall(pdu)
                    finally:
                        if lock:
                            lock.release()
                    logger.info(f"✅ deliver_sm_resp sent via raw socket (seq: {sequence_number}) to {self.smpp_name}")
                    return True
                    
                logger.error(f"❌ Cannot send deliver_sm_resp - no method available")
                return False
        except Exception as e:
            logger.error(f"❌ Error sending deliver_sm_resp: {str(e)}")
            return False
    
    def _process_deliver_sm(self, pdu_data):
        """Process a deliver_sm PDU as DLR"""
        try:
            short_message = pdu_data.get('short_message', '')
            sequence_number = pdu_data.get('sequence_number', 0)
            source_addr = pdu_data.get('source_addr', '')
            destination_addr = pdu_data.get('destination_addr', '')
            esm_class = pdu_data.get('esm_class', 0)
            
            if not short_message:
                logger.warning(f"⚠️ deliver_sm PDU has no short_message for SMPP {self.smpp_id}")
                self._send_deliver_sm_resp(sequence_number, 0)
                return
            
            # Check if this is a delivery receipt
            is_delivery_receipt = (esm_class & 0x04) == 0x04 or 'id:' in short_message.lower() or short_message.startswith('84')
            
            # STEP 1: EXTRACT AND PRINT DLR DETAILS
            print("\n" + "="*100)
            print("📬📬📬 DELIVERY REPORT RECEIVED - DLR METADATA 📬📬📬")
            print("="*100)
            print(f"   🆔 SMPP ID        : {self.smpp_id} ({self.smpp_name})")
            print(f"   🔢 Sequence Number: {sequence_number}")
            print(f"   🕒 Time Received  : {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"   📱 Source SMSC    : {source_addr}")
            print(f"   🎯 Destination    : {destination_addr}")
            print(f"   🎚️ ESM Class      : 0x{esm_class:02x} {'(Delivery Receipt)' if esm_class & 0x04 else ''}")
            print("-" * 100)
            print(f"📊 RAW DLR DATA:")
            print(f"   {repr(short_message)}")
            print(f"   Hex: {binascii.hexlify(short_message.encode('utf-8', errors='ignore')).decode()}")
            print(f"   Length: {len(short_message)} characters")
            print("-" * 100)
            
            if not is_delivery_receipt:
                print(f"📨 Received non-DLR deliver_sm, sending response")
                self._send_deliver_sm_resp(sequence_number, 0)
                return
            
            # Parse DLR based on format
            message_id = None
            status = "UNKNOWN"
            error_code = ""
            
            # Check if this is standard SMPP DLR format
            if 'id:' in short_message.lower() or 'stat:' in short_message.lower():
                # Standard SMPP DLR format
                message_id, status, error_code = self._parse_standard_smpp_dlr(short_message)
            else:
                # Custom/Indian DLR format
                message_id, status, error_code = self._parse_indian_dlr_format(short_message)
            
            # ========== CRITICAL: SEND deliver_sm_resp ==========
            print("\n" + "-" * 100)
            print("🔄 SMPP PROTOCOL REQUIREMENT: Sending deliver_sm_resp")
            print("-" * 100)
            
            resp_sent = self._send_deliver_sm_resp(sequence_number, 0)
            
            if resp_sent:
                print(f"✅ SUCCESS: deliver_sm_resp sent (seq: {sequence_number}) - DLR ACKNOWLEDGED to SMSC")
                print(f"   SMSC will NOT resend this DLR")
            else:
                print(f"❌ FAILED: Could not send deliver_sm_resp - SMSC may resend this DLR")
            
            print("-" * 100)
            
            # ========== DATABASE UPDATE ==========
            if message_id:
                self._find_and_update_message(message_id, status, error_code, short_message, sequence_number, destination_addr)
            else:
                print(f"\n❌ No message_id could be extracted from DLR")
                print(f"   Raw DLR saved for debugging")
                self._handle_unparseable_dlr(short_message, status, error_code, sequence_number, destination_addr)
            
            print("="*100 + "\n")
            
            self.message_count += 1
            
        except Exception as e:
            logger.error(f"❌ Error processing deliver_sm for SMPP {self.smpp_id}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            # Still try to send response
            try:
                self._send_deliver_sm_resp(sequence_number, 0xFF)
            except:
                pass
    
    def _parse_standard_smpp_dlr(self, short_message):
        """Parse standard SMPP DLR format and return (message_id, status, error_code)"""
        print(f"\n🔍 FORMAT: Standard SMPP DLR")
        
        # Extract message_id from id: field
        message_id = None
        id_match = re.search(r'id:([0-9A-Fa-f\-:]+)', short_message, re.IGNORECASE)
        if id_match:
            message_id = id_match.group(1).strip()
            print(f"   📋 Message ID : {message_id}")
        
        # Extract status
        status = "UNKNOWN"
        stat_match = re.search(r'stat:([A-Z]+)', short_message, re.IGNORECASE)
        if stat_match:
            status = stat_match.group(1).upper()
            print(f"   📊 Status     : {status} → {SMPP_STATUS_MAP.get(status, status)}")
        
        # Extract error code
        error_code = ""
        err_match = re.search(r'err:(\d+)', short_message, re.IGNORECASE)
        if err_match:
            error_code = err_match.group(1)
            print(f"   ❌ Error Code : {error_code}")
        
        # Extract sub/dlvrd
        sub_match = re.search(r'sub:(\d+)', short_message, re.IGNORECASE)
        if sub_match:
            print(f"   📤 Submitted  : {sub_match.group(1)}")
        
        dlvrd_match = re.search(r'dlvrd:(\d+)', short_message, re.IGNORECASE)
        if dlvrd_match:
            print(f"   📥 Delivered  : {dlvrd_match.group(1)}")
        
        # Extract dates
        submit_match = re.search(r'submit date:(\d+)', short_message, re.IGNORECASE)
        if submit_match:
            print(f"   📅 Submit Date: {submit_match.group(1)}")
        
        done_match = re.search(r'done date:(\d+)', short_message, re.IGNORECASE)
        if done_match:
            print(f"   📅 Done Date  : {done_match.group(1)}")
        
        return message_id, status, error_code
    
    def _parse_indian_dlr_format(self, short_message):
        """Parse Indian/Telemark DLR format and return (message_id, status, error_code)"""
        print(f"\n🔍 FORMAT: Indian/Telemark DLR")
        
        # Clean the message
        clean_message = short_message.strip()
        message_id = None
        status = "UNKNOWN"
        error_code = ""
        
        # Pattern 1: Check if it starts with "84" or "845" (common in your logs)
        if clean_message.startswith('84'):
            print(f"   Format: Telemark/Indian DLR with prefix '84'")
            
            # Determine status from prefix
            if clean_message.startswith('845'):
                status = "DELIVRD"
                print(f"   📊 Status inferred from prefix '845': DELIVERED")
                content = clean_message[3:]  # Remove "845"
            else:
                status = "DELIVRD"
                print(f"   📊 Status inferred from prefix '84': DELIVERED")
                content = clean_message[2:]  # Remove "84"
            
            # Split by @
            parts = content.split('@')
            if len(parts) >= 2:
                template_id = parts[0]
                rest = '@'.join(parts[1:])
                
                print(f"   📋 Template ID : {template_id}")
                
                # The rest should be hex string
                hash_parts = rest.split('#')
                if len(hash_parts) >= 1:
                    hex_string = hash_parts[0]
                    
                    print(f"   🔑 Hex String  : {hex_string}")
                    print(f"   📏 Hex Length  : {len(hex_string)} chars")
                    
                    # Try to extract UUID from hex
                    uuid_pattern = re.compile(r'([a-f0-9]{8})([a-f0-9]{4})([a-f0-9]{4})([a-f0-9]{4})([a-f0-9]{12})', re.I)
                    match = uuid_pattern.search(hex_string)
                    if match:
                        message_id = f"{match.group(1)}-{match.group(2)}-{match.group(3)}-{match.group(4)}-{match.group(5)}"
                        print(f"   ✅ Extracted UUID: {message_id}")
        
        return message_id, status, error_code
    
    def _find_and_update_message(self, message_id, status, error_code, short_message="", sequence=0, destination_addr=""):
        """
        Find message by ID in ComposeMessageLine and update DLR metadata
        FIXED version with destination_addr parameter
        """
        print(f"\n🔎 DATABASE UPDATE:")
        print(f"   Searching for Message ID: {message_id}")
        print(f"   DLR Sequence Number: {sequence}")
        print(f"   Destination Address: {destination_addr}")
        
        # Clean the message_id (remove any extra characters)
        clean_message_id = message_id.strip()
        
        try:
            with transaction.atomic():
                # Try exact match first
                messages = ComposeMessageLine.objects.filter(message_id__iexact=clean_message_id)
                
                if not messages.exists():
                    # Try without dashes
                    clean_id = clean_message_id.replace('-', '')
                    print(f"   No exact match, trying without dashes: {clean_id[:16]}")
                    messages = ComposeMessageLine.objects.filter(message_id__icontains=clean_id[:16])
                
                if not messages.exists():
                    # Try matching last part of UUID
                    if len(clean_message_id) > 8:
                        short_id = clean_message_id[-8:]  # Last 8 chars
                        print(f"   No match, trying last 8 chars: {short_id}")
                        messages = ComposeMessageLine.objects.filter(message_id__icontains=short_id)
                
                if not messages.exists():
                    # Try matching by sequence number if available
                    if sequence > 0:
                        print(f"   Trying to find by sequence number: {sequence}")
                        messages = ComposeMessageLine.objects.filter(sequence_number=sequence)
                
                if messages.exists():
                    count = messages.count()
                    print(f"   ✅ Found {count} matching message(s)")
                    
                    human_status = SMPP_STATUS_MAP.get(status, status)
                    
                    for msg in messages:
                        old_status = msg.status
                        
                        print(f"\n   📱 Updating message for {msg.mobile_number}:")
                        print(f"      • Line ID    : {msg.line_id}")
                        print(f"      • Message ID : {msg.message_id}")
                        print(f"      • Old Status : {old_status}")
                        print(f"      • New Status : {human_status}")
                        
                        # ===== UPDATE ALL DLR FIELDS IN ComposeMessageLine =====
                        msg.status = human_status
                        msg.dlr_time = timezone.now()
                        
                        # Update reason field with error code if available
                        if error_code:
                            msg.reason = f"DLR Error: {error_code}"
                        else:
                            msg.reason = f"DLR Received: {human_status}"
                        
                        # Update masked_status and masked_reason for API responses
                        msg.masked_status = human_status
                        msg.masked_reason = msg.reason
                        
                        # Store DLR metadata in attributes_5 (JSON field)
                        try:
                            attrs = json.loads(msg.attributes_5) if msg.attributes_5 else {}
                        except:
                            attrs = {}
                        
                        attrs.update({
                            "dlr_received": timezone.now().isoformat(),
                            "dlr_raw_status": status,
                            "dlr_human_status": human_status,
                            "dlr_error_code": error_code,
                            "dlr_sequence": sequence,
                            "dlr_raw_message": short_message[:500],
                            "dlr_processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "smpp_id": str(self.smpp_id),
                            "smpp_name": self.smpp_name,
                            "dlr_destination_addr": destination_addr,
                        })
                        
                        msg.attributes_5 = json.dumps(attrs)
                        
                        # Also store in other attribute fields for redundancy
                        msg.attributes_1 = f"DLR_STATUS:{status}"
                        msg.attributes_2 = f"DLR_TIME:{timezone.now().isoformat()}"
                        msg.attributes_3 = f"DLR_ERROR:{error_code}" if error_code else "DLR_ERROR:NONE"
                        
                        # Save the message
                        msg.save()
                        
                        print(f"\n   ✅ MESSAGE UPDATED SUCCESSFULLY:")
                        print(f"      • Line ID    : {msg.line_id}")
                        print(f"      • Message ID : {msg.message_id}")
                        print(f"      • Mobile     : {msg.mobile_number}")
                        print(f"      • Status     : {old_status} → {human_status}")
                        print(f"      • DLR Time   : {msg.dlr_time.strftime('%Y-%m-%d %H:%M:%S')}")
                        print(f"      • Sequence   : {sequence}")
                        print(f"      • Reason     : {msg.reason}")
                        
                        # Handle credit deduction for DELIVERED status
                        if human_status == "DELIVERED":
                            self._handle_delivered_deduction(msg)
                else:
                    print(f"   ❌ No message found with ID: {clean_message_id}")
                    
                    # Try to find by mobile number as last resort
                    if destination_addr:
                        # Extract mobile number (remove any prefixes like 91)
                        mobile = destination_addr
                        if destination_addr.startswith('91') and len(destination_addr) >= 12:
                            mobile = destination_addr[2:]  # Remove 91 prefix
                        elif destination_addr.startswith('0'):
                            mobile = destination_addr[1:]  # Remove leading 0
                        
                        print(f"   🔍 Attempting to find by mobile: {destination_addr} (cleaned: {mobile})")
                        
                        # Look for messages from last 24 hours with this mobile number
                        recent_msgs = ComposeMessageLine.objects.filter(
                            mobile_number__endswith=mobile[-10:],  # Match last 10 digits
                            submit_time__gte=timezone.now() - timedelta(hours=24)
                        ).order_by('-submit_time')[:5]
                        
                        if recent_msgs.exists():
                            print(f"   📋 Recent messages for this number:")
                            for m in recent_msgs:
                                print(f"      • Line ID: {m.line_id}, Message ID: {m.message_id}, Status: {m.status}, Time: {m.submit_time}")
                        else:
                            print(f"   📋 No recent messages found for {mobile}")
                    
                    # Log the unparseable DLR
                    self._handle_unparseable_dlr(short_message, status, error_code, sequence, destination_addr)
                    
        except Exception as e:
            logger.error(f"❌ Database update error: {str(e)}")
            print(f"   ❌ ERROR: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def _handle_unparseable_dlr(self, raw_message, status, error_code, sequence, destination_addr=""):
        """Handle DLRs that couldn't be matched to a message"""
        try:
            # Create a DLR log entry
            dlr_info = {
                "raw_message": raw_message[:500],
                "status": status,
                "error_code": error_code,
                "sequence": sequence,
                "destination_addr": destination_addr,
                "smpp_id": self.smpp_id,
                "smpp_name": self.smpp_name,
                "session_id": self.session_id,
                "timestamp": timezone.now().isoformat(),
                "dlr_type": "unparseable"
            }
            
            print(f"\n⚠️ UNPARSEABLE DLR STORED:")
            print(f"   • Status     : {status}")
            print(f"   • Error Code : {error_code}")
            print(f"   • Sequence   : {sequence}")
            print(f"   • Destination: {destination_addr}")
            print(f"   • First 100  : {raw_message[:100]}")
            
            # Log to file for debugging
            logger.info(f"📝 Unparseable DLR: {json.dumps(dlr_info)}")
            
        except Exception as e:
            logger.error(f"❌ Error handling unparseable DLR: {str(e)}")
    
    def _handle_delivered_deduction(self, message):
        """
        Handle credit deduction for delivered messages
        FIXED version with proper user_id lookup
        """
        try:
            from .wallet_services import WalletService
            
            # Get the compose message (parent)
            compose_msg = message.compose_message
            if not compose_msg:
                logger.error(f"❌ No compose_message found for line {message.line_id}")
                return
            
            # Get user_id from compose_msg
            user_id = compose_msg.user_id
            if not user_id:
                logger.error(f"❌ No user_id found in compose_message for line {message.line_id}")
                return
            
            # Import User model
            from django.contrib.auth import get_user_model
            User = get_user_model()
            
            try:
                # FIXED: Use user_id field instead of id
                user = User.objects.get(user_id=user_id)
                logger.info(f"✅ Found user: {user.username} (user_id: {user_id})")
            except User.DoesNotExist:
                logger.error(f"❌ User not found with user_id: {user_id}")
                
                # Try alternative lookup methods
                try:
                    # Try using pk (primary key) which might be the default id
                    user = User.objects.get(pk=user_id)
                    logger.info(f"✅ Found user via pk: {user.username}")
                except:
                    logger.error(f"❌ User not found with any method for ID: {user_id}")
                    return
            
            # Get wallet
            wallet = WalletService.get_user_wallet(user)
            if not wallet:
                logger.error(f"❌ No wallet found for user {user.username} (ID: {user_id})")
                return
            
            # Check if deduction type is DELIVERED
            if wallet.deduction_type == "DELIVERED":
                parts = message.parts or 1
                
                # Create credit deduction record
                Credit.objects.create(
                    user=user,
                    action_type='Debit',
                    used_credit=parts,
                    ending_balance=wallet.current_balance - parts,
                    starting_balance=wallet.current_balance,
                    reference_id=message.attributes_2 or message.message_id,
                    comments=f'Delivered message deduction for {message.mobile_number} (DLR received)',
                    create_by='DLR_SYSTEM'
                )
                
                # Update wallet balance
                wallet.current_balance -= parts
                wallet.save()
                
                logger.info(f"💰 Deducted {parts} credits for delivered message to {message.mobile_number}")
                print(f"\n   💰 CREDIT DEDUCTION:")
                print(f"      • Credits    : {parts}")
                print(f"      • Mobile     : {message.mobile_number}")
                print(f"      • User       : {user.username} (user_id: {user_id})")
                print(f"      • New Balance: {wallet.current_balance}")
            else:
                logger.info(f"⏭️ No credit deduction - wallet deduction_type is {wallet.deduction_type}")
                
        except Exception as e:
            logger.error(f"❌ Error handling delivered deduction: {str(e)}")
            import traceback
            traceback.print_exc()
