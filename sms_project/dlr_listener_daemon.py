#!/usr/bin/env python
"""
Standalone DLR Listener Daemon with Query_SM Support
Run this in a separate terminal to:
1. Monitor for delivery receipts (passive DLR)
2. Query pending messages status (active DLR)
"""

import os
import sys
import django
import time
import logging
import threading
import signal
import argparse
import struct
import socket
import json
from datetime import datetime, timedelta
from django.utils import timezone
from django.db import connection

# Setup Django environment
project_path = r'D:\sms_projectV3\sms_projectV1\sms_project'
if project_path not in sys.path:
    sys.path.append(project_path)
    sys.path.append(os.path.join(project_path, 'sms_app'))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sms_project.settings')
django.setup()

# Now import Django models
from sms_app.models import SmppConnection, ComposeMessageLine
from sms_app.smpp_manager import SMPPSessionManager
from sms_app.smpp_dlr_manager import DLRManager

# Configure logging with UTF-8 encoding for Windows
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('dlr_listener.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Disable noisy loggers
logging.getLogger('django').setLevel(logging.WARNING)


class QuerySMHelper:
    """Helper class to handle query_sm operations"""
    
    @staticmethod
    def _recv_exact(sock, n, timeout=10):
        """Receive exact number of bytes"""
        sock.settimeout(timeout)
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Socket closed")
            data += chunk
        return data
    
    @staticmethod
    def query_message_status(session, message_id, source_addr=None, source_addr_ton=5, source_addr_npi=0):
        """
        Send query_sm to check message status
        
        Returns:
            dict with status information or None if failed
        """
        try:
            if not session or not session.bound or not hasattr(session, 'socket'):
                logger.error(f"Invalid session for query_sm")
                return None
            
            # Get sequence number
            seq = session.get_sequence_number()
            
            # Build query_sm PDU
            body_parts = []
            
            # source_addr_ton and source_addr_npi
            body_parts.append(struct.pack(">BB", source_addr_ton, source_addr_npi))
            
            # source_addr (optional)
            if source_addr:
                body_parts.append(source_addr.encode('ascii') + b'\x00')
            else:
                body_parts.append(b'\x00')
            
            # message_id
            body_parts.append(message_id.encode('ascii') + b'\x00')
            
            body = b''.join(body_parts)
            
            # Build complete PDU
            command_id = 0x00000003  # query_sm
            command_status = 0
            total_len = 16 + len(body)
            header = struct.pack(">IIII", total_len, command_id, command_status, seq)
            pdu = header + body
            
            # Send query_sm
            logger.info(f"Sending query_sm for message_id: {message_id[:20]}...")
            session.socket.sendall(pdu)
            
            # Wait for response
            header_resp = QuerySMHelper._recv_exact(session.socket, 16, timeout=10)
            resp_len, resp_cmd, resp_status, resp_seq = struct.unpack(">IIII", header_resp)
            
            # Read response body
            body_resp = b''
            if resp_len > 16:
                body_resp = QuerySMHelper._recv_exact(session.socket, resp_len - 16, timeout=10)
            
            # Parse response
            if resp_cmd == 0x80000003:  # query_sm_resp
                # Parse message_state
                null_pos = body_resp.find(b'\x00')
                if null_pos > 0:
                    resp_message_id = body_resp[:null_pos].decode('ascii', errors='ignore')
                    
                    # Next byte after null is message_state
                    if len(body_resp) > null_pos + 1:
                        message_state = body_resp[null_pos + 1]
                        error_code = body_resp[null_pos + 2] if len(body_resp) > null_pos + 2 else 0
                        
                        # Map message_state to status
                        state_map = {
                            0: "ENROUTE",
                            1: "DELIVERED",
                            2: "EXPIRED",
                            3: "DELETED",
                            4: "UNDELIVERABLE",
                            5: "ACCEPTED",
                            6: "UNKNOWN",
                            7: "REJECTED",
                            8: "SKIPPED"
                        }
                        
                        state_text = state_map.get(message_state, f"UNKNOWN({message_state})")
                        
                        logger.info(f"Query_sm response for {message_id[:20]}: {state_text}")
                        
                        return {
                            'success': True,
                            'message_id': resp_message_id,
                            'message_state': message_state,
                            'state_text': state_text,
                            'error_code': error_code,
                            'command_status': resp_status
                        }
            
            logger.warning(f"Unexpected query_sm response: cmd=0x{resp_cmd:08x}")
            return {
                'success': False,
                'command_status': resp_status,
                'error': f"Unexpected response: 0x{resp_cmd:08x}"
            }
            
        except socket.timeout:
            logger.warning(f"Query_sm timeout for {message_id[:20]}")
            return None
        except Exception as e:
            logger.error(f"Error in query_sm: {e}")
            return None


class PendingMessageChecker(threading.Thread):
    """
    Background thread to check pending messages using query_sm
    """
    
    def __init__(self, check_interval=300):  # Default 5 minutes
        super().__init__(daemon=True, name="PendingMessageChecker")
        self.check_interval = check_interval
        self.running = True
        self.stats = {
            'total_queries': 0,
            'successful_updates': 0,
            'failed_queries': 0
        }
        
    def stop(self):
        """Stop the checker thread"""
        self.running = False
        logger.info("Stopping pending message checker")
    
    def get_pending_messages(self):
        """Get messages that need status check"""
        try:
            # Messages that are:
            # 1. Still in SUBMITTED/ACCEPTED/ENROUTE state
            # 2. Older than 5 minutes (give time for DLR to arrive naturally)
            # 3. Not older than 24 hours
            cutoff_old = timezone.now() - timedelta(hours=24)
            cutoff_min = timezone.now() - timedelta(minutes=5)
            
            pending = ComposeMessageLine.objects.filter(
                status__in=['SUBMITTED', 'ACCEPTED', 'ENROUTE'],
                submit_time__gte=cutoff_old,
                submit_time__lte=cutoff_min
            ).select_related('compose_message')[:200]  # Limit to 200 per run
            
            return pending
            
        except Exception as e:
            logger.error(f"Error fetching pending messages: {e}")
            return []
    
    def get_smpp_connection_for_message(self, message):
        """Get SMPP connection details for a message"""
        try:
            # Try to get from attributes_5 or compose_message attributes_7
            if message.attributes_5:
                try:
                    attrs = json.loads(message.attributes_5)
                    smpp_id = attrs.get('smpp_id')
                    if smpp_id:
                        return SmppConnection.objects.filter(
                            smpp_id=smpp_id,
                            bind_status="BOUND",
                            is_alive=True
                        ).first()
                except:
                    pass
            
            # If not found, check compose_message attributes_7
            if message.compose_message and message.compose_message.attributes_7:
                try:
                    attrs = json.loads(message.compose_message.attributes_7)
                    smpp_id = attrs.get('smpp_id')
                    if smpp_id:
                        return SmppConnection.objects.filter(
                            smpp_id=smpp_id,
                            bind_status="BOUND",
                            is_alive=True
                        ).first()
                except:
                    pass
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting SMPP connection: {e}")
            return None
    
    def update_message_status(self, message, state_text, error_code=""):
        """Update message status in database"""
        try:
            old_status = message.status
            new_status = state_text
            
            # Map to standard status
            if state_text == "DELIVERED":
                new_status = "DELIVERED"
            elif state_text in ["UNDELIVERABLE", "REJECTED", "EXPIRED", "DELETED"]:
                new_status = "FAILED"
            elif state_text in ["ACCEPTED", "ENROUTE"]:
                new_status = state_text
            else:
                new_status = "UNKNOWN"
            
            message.status = new_status
            message.dlr_time = timezone.now()
            message.reason = f"Query_SM: {state_text} (Error: {error_code})"
            
            # Store query info in attributes_5
            try:
                attrs = json.loads(message.attributes_5) if message.attributes_5 else {}
            except:
                attrs = {}
            
            attrs.update({
                "query_sm_time": timezone.now().isoformat(),
                "query_sm_status": state_text,
                "query_sm_error": error_code,
                "query_sm_updated": True
            })
            
            message.attributes_5 = json.dumps(attrs)
            message.save()
            
            logger.info(f"Updated message {message.message_id[:20]}: {old_status} -> {new_status}")
            self.stats['successful_updates'] += 1
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating message status: {e}")
            return False
    
    def run(self):
        """Main checker loop"""
        logger.info(f"Pending message checker started (interval: {self.check_interval}s)")
        
        while self.running:
            try:
                # Get pending messages
                pending_messages = self.get_pending_messages()
                
                if pending_messages:
                    logger.info(f"Found {len(pending_messages)} pending messages to check")
                    
                    for message in pending_messages:
                        if not self.running:
                            break
                        
                        # Get SMPP connection
                        smpp_conn = self.get_smpp_connection_for_message(message)
                        
                        if not smpp_conn:
                            logger.debug(f"No active SMPP connection for message {message.message_id[:20]}")
                            continue
                        
                        # Get session from manager
                        session = SMPPSessionManager.get(smpp_conn.smpp_id)
                        
                        if not session or not session.is_really_connected():
                            logger.warning(f"No valid session for {smpp_conn.connect_name}")
                            continue
                        
                        # Get source address from message
                        source_addr = message.sender or None
                        
                        # Determine TON/NPI (default for alphanumeric)
                        source_ton = 5
                        source_npi = 0
                        
                        # Check if numeric sender
                        if source_addr and source_addr.isdigit():
                            source_ton = 1
                            source_npi = 1
                        
                        # Send query_sm
                        self.stats['total_queries'] += 1
                        
                        result = QuerySMHelper.query_message_status(
                            session=session,
                            message_id=message.message_id,
                            source_addr=source_addr,
                            source_addr_ton=source_ton,
                            source_addr_npi=source_npi
                        )
                        
                        if result and result.get('success'):
                            # Update message status
                            self.update_message_status(
                                message,
                                result['state_text'],
                                str(result.get('error_code', ''))
                            )
                        else:
                            self.stats['failed_queries'] += 1
                            logger.debug(f"No status update for {message.message_id[:20]}")
                        
                        # Small delay between queries
                        time.sleep(1)
                
                # Wait for next check
                for _ in range(self.check_interval):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Error in pending message checker: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(60)
        
        logger.info("Pending message checker stopped")


class DLRDaemon:
    """
    Main DLR Listener Daemon that runs continuously
    """
    
    def __init__(self):
        self.running = True
        self.checked_connections = set()
        self.active_listeners = {}
        self.pending_checker = None
        self.stats = {
            'start_time': time.time(),
            'total_dlrs_processed': 0,
            'total_errors': 0,
            'connections_checked': 0,
            'query_checks': 0
        }
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        logger.info("=" * 80)
        logger.info("DLR LISTENER DAEMON STARTED")
        logger.info(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"\nReceived signal {signum}, shutting down gracefully...")
        self.running = False
        
        # Stop pending message checker
        if self.pending_checker:
            self.pending_checker.stop()
        
        # Stop all DLR listeners
        logger.info("Stopping all DLR listeners...")
        DLRManager.stop_all_listeners()
        
        logger.info("DLR Daemon stopped")
        sys.exit(0)
    
    def check_database_connection(self):
        """Ensure database connection is alive"""
        try:
            connection.ensure_connection()
            return True
        except Exception as e:
            logger.error(f"Database connection error: {e}")
            return False
    
    def get_active_connections(self):
        """Get all active SMPP connections from database"""
        try:
            active_conns = SmppConnection.objects.filter(
                bind_status="BOUND",
                is_alive=True
            ).values(
                'smpp_id', 
                'connect_name', 
                'ip', 
                'tr_trx_port',
                'username',
                'bind_type',
                'account_type',
                'billing_method'
            )
            
            return list(active_conns)
        except Exception as e:
            logger.error(f"Error fetching connections: {e}")
            return []
    
    def get_or_create_session(self, smpp_conn):
        """Get or create SMPP session for connection"""
        try:
            # Try to get existing session
            session = SMPPSessionManager.get(smpp_conn['smpp_id'])
            
            if session and hasattr(session, 'is_really_connected'):
                if session.is_really_connected():
                    logger.info(f"Using existing session for {smpp_conn['connect_name']}")
                    return session
                else:
                    logger.warning(f"Session for {smpp_conn['connect_name']} is dead")
            
            # Create new session
            logger.info(f"Creating new session for {smpp_conn['connect_name']}")
            logger.info(f"   IP: {smpp_conn['ip']}, Port: {smpp_conn['tr_trx_port']}")
            logger.info(f"   Bind Type: {smpp_conn.get('bind_type', 'transceiver')}")
            
            session = SMPPSessionManager.start_session(smpp_conn['smpp_id'])
            
            if session:
                logger.info(f"Session created for {smpp_conn['connect_name']}")
                return session
            else:
                logger.error(f"Failed to create session for {smpp_conn['connect_name']}")
                return None
                
        except Exception as e:
            logger.error(f"Error with session for {smpp_conn['connect_name']}: {e}")
            return None
    
    def start_listener_for_connection(self, smpp_conn, session):
        """Start DLR listener for a connection"""
        try:
            # Check if listener already exists
            existing_listener = DLRManager.get_listener(smpp_conn['smpp_id'])
            if existing_listener:
                if existing_listener.is_alive():
                    logger.info(f"Listener already running for {smpp_conn['connect_name']}")
                    return existing_listener
                else:
                    logger.warning(f"Listener for {smpp_conn['connect_name']} is dead")
            
            # Start new listener
            listener = DLRManager.start_listener(session)
            
            if listener:
                logger.info(f"DLR listener started for {smpp_conn['connect_name']}")
                return listener
            else:
                logger.error(f"Failed to start listener for {smpp_conn['connect_name']}")
                return None
                
        except Exception as e:
            logger.error(f"Error starting listener for {smpp_conn['connect_name']}: {e}")
            return None
    
    def monitor_listeners(self):
        """Monitor all active listeners and report status"""
        try:
            status_list = DLRManager.get_status()
            
            if status_list:
                logger.info("\n" + "-" * 60)
                logger.info("ACTIVE DLR LISTENERS:")
                for status in status_list:
                    smpp_name = status.get('smpp_name', f'SMPP-{status["smpp_id"]}')
                    logger.info(f"   - {smpp_name} (ID: {status['smpp_id']})")
                    logger.info(f"     * Processed: {status['messages_processed']} DLRs")
                    logger.info(f"     * Last Activity: {status['last_activity']}")
                    logger.info(f"     * Errors: {status['errors']}")
                    logger.info(f"     * Status: {'ALIVE' if status['alive'] else 'DEAD'}")
                logger.info("-" * 60)
            else:
                logger.info("No active DLR listeners")
                
        except Exception as e:
            logger.error(f"Error monitoring listeners: {e}")
    
    def print_stats(self):
        """Print daemon statistics"""
        uptime = time.time() - self.stats['start_time']
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        seconds = int(uptime % 60)
        
        logger.info("\n" + "=" * 70)
        logger.info("DAEMON STATISTICS:")
        logger.info(f"   Uptime: {hours}h {minutes}m {seconds}s")
        logger.info(f"   DLRs Processed: {self.stats['total_dlrs_processed']}")
        
        if self.pending_checker:
            logger.info(f"   Query Checks: {self.pending_checker.stats['total_queries']}")
            logger.info(f"   Updates via Query: {self.pending_checker.stats['successful_updates']}")
            logger.info(f"   Failed Queries: {self.pending_checker.stats['failed_queries']}")
        
        logger.info(f"   Connections: {self.stats['connections_checked']}")
        logger.info(f"   Total Errors: {self.stats['total_errors']}")
        
        # Update stats from listeners
        try:
            status_list = DLRManager.get_status()
            total_dlrs = sum(s.get('messages_processed', 0) for s in status_list)
            self.stats['total_dlrs_processed'] = total_dlrs
        except:
            pass
        
        logger.info("=" * 70)
    
    def run_once(self):
        """Run one iteration of checking connections"""
        try:
            # Check database connection
            if not self.check_database_connection():
                logger.error("Database connection failed, waiting...")
                time.sleep(10)
                return
            
            # Get active connections
            active_conns = self.get_active_connections()
            self.stats['connections_checked'] = len(active_conns)
            
            if not active_conns:
                logger.info("No active SMPP connections found, waiting...")
                time.sleep(10)
                return
            
            logger.info(f"\nFound {len(active_conns)} active SMPP connections")
            
            # Process each connection
            for conn in active_conns:
                conn_id = conn['smpp_id']
                conn_name = conn['connect_name']
                
                logger.info(f"\nProcessing connection: {conn_name} (ID: {conn_id})")
                logger.info(f"   IP: {conn.get('ip', 'N/A')}, Port: {conn.get('tr_trx_port', 'N/A')}")
                logger.info(f"   Bind Type: {conn.get('bind_type', 'N/A')}")
                logger.info(f"   Account Type: {conn.get('account_type', 'N/A')}")
                
                # Get or create session
                session = self.get_or_create_session(conn)
                
                if session:
                    # Start listener
                    listener = self.start_listener_for_connection(conn, session)
                    
                    if listener:
                        self.active_listeners[conn_id] = {
                            'name': conn_name,
                            'listener': listener,
                            'session': session,
                            'last_check': time.time()
                        }
                        
                        # Update stats from listener
                        if hasattr(listener, 'message_count'):
                            self.stats['total_dlrs_processed'] += listener.message_count
                else:
                    logger.error(f"Could not create session for {conn_name}")
                    self.stats['total_errors'] += 1
                
                # Small delay between connections
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Error in run_once: {e}")
            self.stats['total_errors'] += 1
            import traceback
            traceback.print_exc()
    
    def run(self):
        """Main daemon loop"""
        # Start pending message checker thread
        self.pending_checker = PendingMessageChecker(check_interval=300)  # Check every 5 minutes
        self.pending_checker.start()
        
        last_stats_time = time.time()
        last_monitor_time = time.time()
        
        while self.running:
            try:
                # Main check loop
                self.run_once()
                
                # Print stats every 5 minutes
                if time.time() - last_stats_time > 300:  # 5 minutes
                    self.print_stats()
                    last_stats_time = time.time()
                
                # Monitor listeners every 2 minutes
                if time.time() - last_monitor_time > 120:  # 2 minutes
                    self.monitor_listeners()
                    last_monitor_time = time.time()
                
                # Wait before next check
                logger.info("\nWaiting 30 seconds before next check...")
                for i in range(30):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                logger.info("\nReceived keyboard interrupt")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                self.stats['total_errors'] += 1
                import traceback
                traceback.print_exc()
                time.sleep(10)
        
        # Cleanup
        logger.info("Shutting down DLR Daemon...")
        if self.pending_checker:
            self.pending_checker.stop()
        DLRManager.stop_all_listeners()
        logger.info("DLR Daemon stopped")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='SMPP DLR Listener Daemon with Query_SM')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--interval', type=int, default=30, help='Connection check interval in seconds')
    parser.add_argument('--query-interval', type=int, default=300, help='Query_SM check interval in seconds')
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("Debug mode enabled")
    
    logger.info(f"Check interval: {args.interval} seconds")
    logger.info(f"Query interval: {args.query_interval} seconds")
    
    # Create and run daemon
    daemon = DLRDaemon()
    
    try:
        daemon.run()
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()