# session_manager.py - UPDATED VERSION WITH TIMEOUTS
from typing import Dict, Optional, Tuple
from .models import SmppConnection
from .smpp_client import FixedSMPPClient
import threading
import logging
import time
import socket

logger = logging.getLogger('session_manager')

class SMPPSessionManager:
    _sessions: Dict[int, FixedSMPPClient] = {}
    _session_lock = threading.Lock()
    _session_info: Dict[int, Dict] = {}
    
    # Timeout settings
    CONNECTION_CHECK_TIMEOUT = 2.0  # 2 seconds max for connection check
    SOCKET_TIMEOUT = 1.0  # 1 second socket timeout for checks
    
    @classmethod
    def _safe_is_connected(cls, session: FixedSMPPClient) -> bool:
        """Safely check if session is connected with timeout protection"""
        try:
            # Set a timeout for the check
            original_timeout = None
            if hasattr(session, 'socket') and session.socket:
                original_timeout = session.socket.gettimeout()
                session.socket.settimeout(cls.SOCKET_TIMEOUT)
            
            # Quick check without network I/O if possible
            if not hasattr(session, 'connected') or not session.connected:
                return False
            if not hasattr(session, 'bound') or not session.bound:
                return False
            
            # Only do network check if we have a socket
            if hasattr(session, 'socket') and session.socket:
                # Try a quick peek to see if socket is readable
                import select
                ready_to_read, _, _ = select.select([session.socket], [], [], 0.1)
                if ready_to_read:
                    # There's data waiting, which is unusual for idle session
                    logger.warning(f"Socket for session {id(session)} has pending data")
            
            return True
            
        except socket.timeout:
            logger.debug(f"Socket timeout checking session {id(session)}")
            return False
        except (socket.error, OSError) as e:
            logger.debug(f"Socket error checking session {id(session)}: {e}")
            return False
        except Exception as e:
            logger.debug(f"Error checking session {id(session)}: {e}")
            return False
        finally:
            # Restore original timeout
            if hasattr(session, 'socket') and session.socket and original_timeout is not None:
                try:
                    session.socket.settimeout(original_timeout)
                except:
                    pass
    
    @classmethod
    def get(cls, smpp_id: int, timeout: float = None) -> Optional[FixedSMPPClient]:
        """Get an existing session with timeout protection"""
        if timeout is None:
            timeout = cls.CONNECTION_CHECK_TIMEOUT
        
        start_time = time.time()
        
        with cls._session_lock:
            session = cls._sessions.get(smpp_id)
            if not session:
                logger.debug(f"No session found for smpp_id: {smpp_id}")
                return None
            
            # Check elapsed time
            elapsed = time.time() - start_time
            if elapsed > timeout:
                logger.warning(f"Timeout getting session {smpp_id} (took {elapsed:.2f}s)")
                return session  # Return session anyway, let caller handle
            
            logger.debug(f"Found session for smpp_id: {smpp_id}")
            logger.debug(f"Session attributes: connected={getattr(session, 'connected', 'N/A')}, "
                        f"bound={getattr(session, 'bound', 'N/A')}")
        
        # Do connection check outside lock to avoid blocking
        try:
            # Quick attribute check first
            if not getattr(session, 'connected', False):
                logger.debug(f"Session {smpp_id} marked as not connected")
                return None
            
            if not getattr(session, 'bound', False):
                logger.debug(f"Session {smpp_id} marked as not bound")
                return None
            
            # Quick socket check (non-blocking)
            if cls._safe_is_connected(session):
                logger.debug(f"Session {smpp_id} is alive")
                return session
            else:
                logger.debug(f"Session {smpp_id} failed quick health check")
                # Don't try to reconnect here - let caller decide
                return session
                
        except Exception as e:
            logger.debug(f"Error checking session {smpp_id}: {e}")
            return session  # Return session anyway
    
    @classmethod
    def start_session(cls, smpp_id: int, timeout: float = 10.0) -> Optional[FixedSMPPClient]:
        """Start a new session with timeout protection"""
        start_time = time.time()
        
        with cls._session_lock:
            # First check if session already exists (quick check)
            existing = cls._sessions.get(smpp_id)
            if existing:
                logger.info(f"Session already exists for smpp_id: {smpp_id}")
                # Quick attribute check
                if getattr(existing, 'connected', False) and getattr(existing, 'bound', False):
                    logger.info(f"Existing session looks good for smpp_id: {smpp_id}")
                    return existing
        
        try:
            # Check timeout
            if time.time() - start_time > timeout:
                logger.error(f"Timeout before starting session for smpp_id: {smpp_id}")
                return None
            
            conn = SmppConnection.objects.get(pk=smpp_id)
            logger.info(f"Creating new session for smpp_id: {smpp_id}, connection: {conn.connect_name}")
            
            # Create new session
            session = FixedSMPPClient(conn)
            
            # Connect with timeout
            connect_timeout = min(5.0, timeout - (time.time() - start_time))
            if connect_timeout <= 0:
                logger.error(f"Timeout for connect phase smpp_id: {smpp_id}")
                return None
            
            # We need to modify FixedSMPPClient.connect() to accept timeout
            # For now, use default timeout
            if not session.connect():
                logger.error(f"Failed to connect for smpp_id: {smpp_id}")
                return None
            
            # Check timeout again
            if time.time() - start_time > timeout:
                logger.error(f"Timeout before bind for smpp_id: {smpp_id}")
                session.force_close()
                return None
            
            # Bind with timeout
            bind_timeout = min(5.0, timeout - (time.time() - start_time))
            if bind_timeout <= 0:
                logger.error(f"Timeout for bind phase smpp_id: {smpp_id}")
                session.force_close()
                return None
            
            if session.bind_connection():
                with cls._session_lock:
                    cls._sessions[smpp_id] = session
                    cls._session_info[smpp_id] = {
                        'created_at': time.time(),
                        'connection_name': conn.connect_name,
                        'bind_type': conn.bind_type,
                        'username': conn.username
                    }
                logger.info(f"New session created successfully for smpp_id: {smpp_id}")
                return session
            else:
                logger.error(f"Failed to bind new session for smpp_id: {smpp_id}")
                session.force_close()
                return None
                
        except SmppConnection.DoesNotExist:
            logger.error(f"SmppConnection with id {smpp_id} does not exist")
            return None
        except Exception as e:
            logger.exception(f"Failed to start session for smpp_id: {smpp_id}: {e}")
            return None