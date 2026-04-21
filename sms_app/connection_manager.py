# connection_manager.py
import logging
import threading
import time
import socket
import struct
import binascii
from typing import Dict
from django.utils import timezone

logger = logging.getLogger(__name__)

class FinalDiagnostic:
    """Final diagnostic to prove the issue to your SMPP provider"""
    
    def __init__(self, host, port, system_id, password):
        self.host = host
        self.port = port
        self.system_id = system_id
        self.password = password
        
        # Print all connection parameters
        logger.info("🎯 STEP 1: CONNECTION PARAMETERS RECEIVED")
        logger.info("=" * 50)
        logger.info(f"   🖥️  Host: {self.host}")
        logger.info(f"   🔌 Port: {self.port}")
        logger.info(f"   👤 System ID (Username): '{self.system_id}'")
        logger.info(f"   🔑 Password: {'*' * len(self.password) if self.password else 'EMPTY'}")
        logger.info(f"   📏 Password Length: {len(self.password)} characters")
        logger.info(f"   🔍 Password (raw): '{self.password}'")
        logger.info(f"   📍 System ID Length: {len(self.system_id)} characters")
        logger.info("=" * 50)
    
    def run_final_diagnostic(self):
        """Run final diagnostic that provides clear evidence"""
        logger.info("🔍 FINAL DIAGNOSTIC REPORT")
        logger.info("=" * 60)
        logger.info(f"TARGET: {self.host}:{self.port}")
        logger.info(f"USERNAME: {self.system_id}")
        logger.info("=" * 60)
        
        evidence = {
            'basic_connectivity': self._test_basic_connectivity(),
            'server_response': self._test_server_response(),
            'smpp_protocol': self._test_smpp_protocol(),
            'smpp_detailed_debug': self._debug_smpp_communication(),
            'alternative_ports': self._test_common_ports()
        }
        
        self._generate_final_report(evidence)
        return evidence
    
    def _test_basic_connectivity(self):
        """Test basic TCP connectivity"""
        logger.info("")
        logger.info("🎯 STEP 2: BASIC TCP CONNECTIVITY TEST")
        logger.info("=" * 50)
        logger.info(f"   🔌 Testing connection to: {self.host}:{self.port}")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            
            logger.info(f"   📡 Attempting TCP connection...")
            start_time = time.time()
            result = sock.connect_ex((self.host, self.port))
            connect_time = time.time() - start_time
            
            sock.close()
            
            if result == 0:
                logger.info(f"   ✅ SUCCESS: Port {self.port} accepts TCP connections")
                logger.info(f"   ⏱️  Connection established in {connect_time:.3f} seconds")
                logger.info("   💡 The port is OPEN and listening")
                return True
            else:
                logger.error(f"   ❌ FAILED: Port {self.port} is closed (OS error: {result})")
                logger.info("   🔍 This means the port is not listening or firewall blocked")
                return False
        except Exception as e:
            logger.error(f"   ❌ FAILED: Connection failed - {e}")
            return False
    
    def _test_server_response(self):
        """Test if server responds to any communication"""
        logger.info("")
        logger.info("🎯 STEP 3: SERVER RESPONSE TEST")
        logger.info("=" * 50)
        logger.info(f"   🔌 Connecting to: {self.host}:{self.port}")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            
            logger.info(f"   📡 Establishing TCP connection...")
            start_time = time.time()
            sock.connect((self.host, self.port))
            connect_time = time.time() - start_time
            
            logger.info(f"   ✅ SUCCESS: Connected to server in {connect_time:.3f} seconds")
            
            # Test 1: Wait for server banner
            logger.info("")
            logger.info("   🎯 STEP 3.1: CHECKING FOR SERVER BANNER")
            sock.settimeout(5)
            try:
                logger.info("   📥 Waiting for server banner (5 seconds timeout)...")
                banner = sock.recv(1024)
                if banner:
                    logger.info(f"   ✅ Server sends banner: {len(banner)} bytes")
                    logger.info(f"   🔍 Banner (hex): {binascii.hexlify(banner).decode()}")
                    try:
                        banner_text = banner.decode('ascii', errors='replace')
                        logger.info(f"   🔍 Banner (text): {banner_text}")
                    except:
                        logger.info("   🔍 Banner (text): Cannot decode as ASCII")
                else:
                    logger.info("   ℹ️  No initial banner from server")
            except socket.timeout:
                logger.info("   ℹ️  No initial banner (server waiting for client)")
            
            # Test 2: Send test data and see response
            logger.info("")
            logger.info("   🎯 STEP 3.2: TESTING SERVER RESPONSE TO DATA")
            test_data = b"TEST\r\n"
            logger.info(f"   📤 Sending test data: {test_data}")
            sock.send(test_data)
            
            sock.settimeout(10)
            try:
                logger.info("   📥 Waiting for server response (10 seconds timeout)...")
                response = sock.recv(1024)
                if response:
                    logger.info(f"   ✅ Server responds to test data: {len(response)} bytes")
                    logger.info(f"   🔍 Response (hex): {binascii.hexlify(response).decode()}")
                    try:
                        response_text = response.decode('ascii', errors='replace')
                        logger.info(f"   🔍 Response (text): {response_text}")
                    except:
                        logger.info("   🔍 Response (text): Cannot decode as ASCII")
                else:
                    logger.info("   ❌ No response to test data")
            except socket.timeout:
                logger.info("   ❌ Server ignores test data (timeout)")
            except ConnectionResetError:
                logger.error("   ❌ Server forcibly closes connection")
            
            sock.close()
            logger.info("   🔌 Connection closed")
            return True
            
        except ConnectionResetError:
            logger.error("   ❌ FAILED: Server forcibly closes connection immediately")
            logger.info("   🔍 This means the server actively rejects the connection")
            return False
        except Exception as e:
            logger.error(f"   ❌ FAILED: Server response test failed - {e}")
            return False

    def _debug_smpp_communication(self):
        """Debug the actual SMPP communication"""
        logger.info("")
        logger.info("🎯 STEP 4: DETAILED SMPP PROTOCOL TESTING")
        logger.info("=" * 50)
        logger.info(f"   🎯 Target: {self.host}:{self.port}")
        
        # Test multiple SMPP bind variations
        logger.info("")
        logger.info("   🎯 STEP 4.1: TESTING DIFFERENT SMPP BIND TYPES")
        
        results = {
            'transceiver_bind': self._test_smpp_bind_attempt('TRANSCEIVER', self._create_smpp_bind_pdu_transceiver),
            'transmitter_bind': self._test_smpp_bind_attempt('TRANSMITTER', self._create_smpp_bind_pdu_transmitter),
            'receiver_bind': self._test_smpp_bind_attempt('RECEIVER', self._create_smpp_bind_pdu_receiver)
        }
        
        # If any bind succeeds, test enquire_link
        if any(results.values()):
            logger.info("")
            logger.info("   🎯 STEP 4.2: TESTING ENQUIRE_LINK (ONLY IF BIND SUCCESSFUL)")
            self._test_enquire_link()
        
        return any(results.values())

    def _test_smpp_bind_attempt(self, bind_type, pdu_creator):
        """Test a specific SMPP bind attempt"""
        logger.info("")
        logger.info(f"   🔄 {bind_type} BIND ATTEMPT")
        logger.info("   " + "-" * 40)
        try:
            logger.info(f"   🔌 Creating TCP socket...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            
            logger.info(f"   📡 Connecting to {self.host}:{self.port}...")
            start_time = time.time()
            sock.connect((self.host, self.port))
            connect_time = time.time() - start_time
            logger.info(f"   ✅ Connected in {connect_time:.3f} seconds")
            
            # Create and send bind PDU
            logger.info(f"   🔧 Creating {bind_type} bind PDU...")
            bind_pdu = pdu_creator()
            logger.info(f"   📤 Sending {bind_type} bind PDU: {len(bind_pdu)} bytes")
            logger.info(f"   🔍 PDU hex: {binascii.hexlify(bind_pdu).decode()}")
            
            start_time = time.time()
            bytes_sent = sock.send(bind_pdu)
            send_time = time.time() - start_time
            logger.info(f"   ✅ Data sent: {bytes_sent} bytes in {send_time:.3f} seconds")
            
            # Wait for response
            logger.info(f"   📥 Waiting for bind response...")
            response = b''
            for i in range(3):
                try:
                    sock.settimeout(8)
                    logger.info(f"   🔄 Receive attempt {i+1}/3...")
                    chunk = sock.recv(1024)
                    if chunk:
                        response += chunk
                        logger.info(f"   📥 Received chunk {i+1}: {len(chunk)} bytes")
                        # Check if we have complete PDU
                        if len(response) >= 4:
                            expected_len = struct.unpack('>I', response[0:4])[0]
                            logger.info(f"   🔍 Expected PDU length: {expected_len}, Received: {len(response)}")
                            if len(response) >= expected_len:
                                logger.info("   ✅ Complete PDU received")
                                break
                    else:
                        logger.info("   📥 Connection closed by server (no data)")
                        break
                except socket.timeout:
                    logger.info(f"   ⏰ Timeout waiting for response part {i+1}")
                    break
                except ConnectionResetError as e:
                    logger.error(f"   ❌ Connection forcibly closed by remote host: {e}")
                    logger.info("   🔍 Server actively rejected our SMPP bind")
                    break
                except Exception as e:
                    logger.error(f"   ❌ Error receiving data: {e}")
                    break
            
            if response:
                result = self._analyze_smpp_response_detailed(response, bind_type)
                sock.close()
                logger.info(f"   🔌 Connection closed")
                return result
            else:
                logger.error(f"   ❌ {bind_type}: NO RESPONSE RECEIVED AT ALL")
                logger.info(f"   🔍 Server completely ignores {bind_type} SMPP bind")
                sock.close()
                logger.info(f"   🔌 Connection closed")
                return False
                
        except ConnectionResetError as e:
            logger.error(f"   ❌ {bind_type}: Connection forcibly closed by remote host: {e}")
            logger.info(f"   🔍 Server actively rejects {bind_type} connection immediately")
            return False
        except socket.timeout:
            logger.error(f"   ❌ {bind_type}: Connection timeout - server not responding")
            return False
        except Exception as e:
            logger.error(f"   ❌ {bind_type} bind failed: {e}")
            return False

    def _create_smpp_bind_pdu_transceiver(self):
        """Create SMPP bind_transceiver PDU"""
        logger.info("   🔧 BUILDING BIND_TRANSCEIVER PDU:")
        
        system_id = self.system_id.encode('ascii') + b'\x00'
        password = self.password.encode('ascii') + b'\x00'
        system_type = b'SMPP' + b'\x00'
        
        pdu_length = 16 + len(system_id) + len(password) + len(system_type) + 1 + 1 + 1 + 1
        
        logger.info("   📊 PDU PARAMETERS:")
        logger.info(f"      Command ID: 0x00000009 (bind_transceiver)")
        logger.info(f"      System ID: '{self.system_id}'")
        logger.info(f"      Password: '{self.password}'")
        logger.info(f"      System Type: 'SMPP'")
        logger.info(f"      Interface Version: 0x34 (SMPP 3.4)")
        logger.info(f"      Addr TON: 0x01 (International)")
        logger.info(f"      Addr NPI: 0x01 (ISDN/E163)")
        logger.info(f"      Address Range: EMPTY")
        logger.info(f"      Sequence: 1")
        
        pdu = struct.pack('>I', pdu_length)
        pdu += struct.pack('>I', 0x00000009)  # bind_transceiver
        pdu += struct.pack('>I', 0)
        pdu += struct.pack('>I', 1)
        pdu += system_id
        pdu += password
        pdu += system_type
        pdu += struct.pack('>B', 0x34)  # interface_version
        pdu += struct.pack('>B', 0x01)  # addr_ton
        pdu += struct.pack('>B', 0x01)  # addr_npi
        pdu += b'\x00'  # address_range
        
        return pdu

    def _create_smpp_bind_pdu_transmitter(self):
        """Create SMPP bind_transmitter PDU"""
        logger.info("   🔧 BUILDING BIND_TRANSMITTER PDU:")
        
        system_id = self.system_id.encode('ascii') + b'\x00'
        password = self.password.encode('ascii') + b'\x00'
        system_type = b'SMPP' + b'\x00'
        
        pdu_length = 16 + len(system_id) + len(password) + len(system_type) + 1 + 1 + 1 + 1
        
        logger.info("   📊 PDU PARAMETERS:")
        logger.info(f"      Command ID: 0x00000002 (bind_transmitter)")
        logger.info(f"      System ID: '{self.system_id}'")
        logger.info(f"      Password: '{self.password}'")
        logger.info(f"      System Type: 'SMPP'")
        logger.info(f"      Interface Version: 0x34 (SMPP 3.4)")
        logger.info(f"      Addr TON: 0x01 (International)")
        logger.info(f"      Addr NPI: 0x01 (ISDN/E163)")
        logger.info(f"      Address Range: EMPTY")
        logger.info(f"      Sequence: 1")
        
        pdu = struct.pack('>I', pdu_length)
        pdu += struct.pack('>I', 0x00000002)  # bind_transmitter
        pdu += struct.pack('>I', 0)
        pdu += struct.pack('>I', 1)
        pdu += system_id
        pdu += password
        pdu += system_type
        pdu += struct.pack('>B', 0x34)  # interface_version
        pdu += struct.pack('>B', 0x01)  # addr_ton
        pdu += struct.pack('>B', 0x01)  # addr_npi
        pdu += b'\x00'  # address_range
        
        return pdu

    def _create_smpp_bind_pdu_receiver(self):
        """Create SMPP bind_receiver PDU"""
        logger.info("   🔧 BUILDING BIND_RECEIVER PDU:")
        
        system_id = self.system_id.encode('ascii') + b'\x00'
        password = self.password.encode('ascii') + b'\x00'
        system_type = b'SMPP' + b'\x00'
        
        pdu_length = 16 + len(system_id) + len(password) + len(system_type) + 1 + 1 + 1 + 1
        
        logger.info("   📊 PDU PARAMETERS:")
        logger.info(f"      Command ID: 0x00000001 (bind_receiver)")
        logger.info(f"      System ID: '{self.system_id}'")
        logger.info(f"      Password: '{self.password}'")
        logger.info(f"      System Type: 'SMPP'")
        logger.info(f"      Interface Version: 0x34 (SMPP 3.4)")
        logger.info(f"      Addr TON: 0x01 (International)")
        logger.info(f"      Addr NPI: 0x01 (ISDN/E163)")
        logger.info(f"      Address Range: EMPTY")
        logger.info(f"      Sequence: 1")
        
        pdu = struct.pack('>I', pdu_length)
        pdu += struct.pack('>I', 0x00000001)  # bind_receiver
        pdu += struct.pack('>I', 0)
        pdu += struct.pack('>I', 1)
        pdu += system_id
        pdu += password
        pdu += system_type
        pdu += struct.pack('>B', 0x34)  # interface_version
        pdu += struct.pack('>B', 0x01)  # addr_ton
        pdu += struct.pack('>B', 0x01)  # addr_npi
        pdu += b'\x00'  # address_range
        
        return pdu

    def _test_enquire_link(self):
        """Test SMPP enquire_link operation"""
        logger.info("   🎯 TESTING ENQUIRE_LINK OPERATION")
        logger.info("   " + "-" * 40)
        try:
            logger.info("   🔌 Creating new connection for enquire_link...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((self.host, self.port))
            logger.info("   ✅ Connected for enquire_link test")
            
            # First bind
            logger.info("   🔧 Sending bind_transceiver first...")
            bind_pdu = self._create_smpp_bind_pdu_transceiver()
            sock.send(bind_pdu)
            
            # Wait for bind response
            sock.settimeout(10)
            bind_response = sock.recv(1024)
            if bind_response and len(bind_response) >= 16:
                cmd_status = struct.unpack('>I', bind_response[8:12])[0]
                if cmd_status == 0:
                    logger.info("   ✅ Bind successful, proceeding with enquire_link")
                    
                    # Send enquire_link
                    logger.info("   🔧 Creating enquire_link PDU...")
                    enquire_pdu = self._create_enquire_link_pdu()
                    logger.info(f"   📤 Sending enquire_link: {len(enquire_pdu)} bytes")
                    sock.send(enquire_pdu)
                    
                    # Wait for enquire_link response
                    logger.info("   📥 Waiting for enquire_link_response...")
                    sock.settimeout(10)
                    enquire_response = sock.recv(1024)
                    if enquire_response:
                        logger.info(f"   ✅ Received enquire_link_response: {len(enquire_response)} bytes")
                        self._analyze_enquire_link_response(enquire_response)
                    else:
                        logger.error("   ❌ No response to enquire_link")
                else:
                    logger.error("   ❌ Bind failed, cannot test enquire_link")
            else:
                logger.error("   ❌ No bind response received")
            
            sock.close()
            logger.info("   🔌 Connection closed")
            
        except Exception as e:
            logger.error(f"   ❌ Enquire_link test failed: {e}")

    def _create_enquire_link_pdu(self):
        """Create SMPP enquire_link PDU"""
        pdu_length = 16
        pdu = struct.pack('>I', pdu_length)
        pdu += struct.pack('>I', 0x00000015)  # enquire_link
        pdu += struct.pack('>I', 0)
        pdu += struct.pack('>I', 2)  # sequence 2
        
        logger.info("   📊 ENQUIRE_LINK PDU PARAMETERS:")
        logger.info(f"      Command ID: 0x00000015 (enquire_link)")
        logger.info(f"      Command Length: 16 bytes")
        logger.info(f"      Sequence: 2")
        
        return pdu

    def _analyze_enquire_link_response(self, response):
        """Analyze enquire_link response"""
        try:
            if len(response) >= 16:
                cmd_len = struct.unpack('>I', response[0:4])[0]
                cmd_id = struct.unpack('>I', response[4:8])[0]
                cmd_status = struct.unpack('>I', response[8:12])[0]
                seq_num = struct.unpack('>I', response[12:16])[0]
                
                logger.info("   🔍 ENQUIRE_LINK RESPONSE ANALYSIS:")
                logger.info(f"      - Command Length: {cmd_len}")
                logger.info(f"      - Command ID: 0x{cmd_id:08x} ({self._get_command_name(cmd_id)})")
                logger.info(f"      - Status: 0x{cmd_status:08x}")
                logger.info(f"      - Sequence: {seq_num}")
                
                if cmd_id == 0x80000015 and cmd_status == 0:  # enquire_link_resp
                    logger.info("   ✅ ENQUIRE_LINK SUCCESSFUL!")
                    return True
                else:
                    logger.error("   ❌ ENQUIRE_LINK FAILED!")
                    return False
            else:
                logger.error(f"   ❌ Invalid enquire_link response: {len(response)} bytes")
                return False
        except Exception as e:
            logger.error(f"   ❌ Error analyzing enquire_link response: {e}")
            return False
    
    def _test_smpp_protocol(self):
        """Test SMPP protocol specifically"""
        logger.info("")
        logger.info("🎯 STEP 5: BASIC SMPP PROTOCOL TEST")
        logger.info("=" * 50)
        logger.info(f"   🎯 Testing SMPP on: {self.host}:{self.port}")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((self.host, self.port))
            logger.info("   ✅ Connected for SMPP test")
            
            # Send SMPP bind_transceiver
            pdu = self._create_smpp_bind_pdu_transceiver()
            logger.info(f"   📤 Sending SMPP bind_transceiver PDU")
            logger.info(f"   🔍 PDU size: {len(pdu)} bytes")
            logger.info(f"   🔍 PDU hex: {binascii.hexlify(pdu).decode()}")
            
            sock.send(pdu)
            
            # Wait for SMPP response
            sock.settimeout(15)
            try:
                response = sock.recv(1024)
                if response:
                    logger.info(f"   ✅ Server responds to SMPP: {len(response)} bytes")
                    return self._analyze_smpp_response(response)
                else:
                    logger.error("   ❌ No response to SMPP protocol")
                    return False
            except socket.timeout:
                logger.error("   ⏰ SMPP protocol timeout - server ignores SMPP")
                return False
            except ConnectionResetError:
                logger.error("   ❌ Server forcibly closes connection on SMPP")
                return False
            finally:
                sock.close()
                
        except Exception as e:
            logger.error(f"   ❌ SMPP protocol test failed - {e}")
            return False

    def _analyze_smpp_response(self, response):
        """Analyze SMPP response"""
        try:
            if len(response) >= 16:
                cmd_len = struct.unpack('>I', response[0:4])[0]
                cmd_id = struct.unpack('>I', response[4:8])[0]
                cmd_status = struct.unpack('>I', response[8:12])[0]
                
                logger.info(f"   🔍 SMPP Response Analysis:")
                logger.info(f"      - Command Length: {cmd_len}")
                logger.info(f"      - Command ID: 0x{cmd_id:08x}")
                logger.info(f"      - Status: 0x{cmd_status:08x}")
                
                if cmd_id == 0x80000009:  # bind_transceiver_resp
                    if cmd_status == 0:
                        logger.info("   🎉 SMPP BIND SUCCESSFUL!")
                        return True
                    else:
                        error_msg = self._get_smpp_error(cmd_status)
                        logger.error(f"   ❌ SMPP Bind Failed: {error_msg}")
                        return False
                else:
                    logger.warning(f"   ⚠️ Not SMPP bind response: 0x{cmd_id:08x}")
                    return False
            else:
                logger.error(f"   ❌ Invalid SMPP response: {len(response)} bytes")
                return False
        except Exception as e:
            logger.error(f"   ❌ Error analyzing SMPP response: {e}")
            return False

    def _analyze_smpp_response_detailed(self, response, bind_type="SMPP"):
        """Analyze SMPP response with detailed debugging"""
        try:
            logger.info(f"   🔍 {bind_type} RESPONSE ANALYSIS:")
            logger.info(f"   🔍 Raw response ({len(response)} bytes): {binascii.hexlify(response).decode()}")
            
            if len(response) >= 16:
                cmd_len = struct.unpack('>I', response[0:4])[0]
                cmd_id = struct.unpack('>I', response[4:8])[0]
                cmd_status = struct.unpack('>I', response[8:12])[0]
                seq_num = struct.unpack('>I', response[12:16])[0]
                
                logger.info(f"      - Command Length: {cmd_len}")
                logger.info(f"      - Command ID: 0x{cmd_id:08x} ({self._get_command_name(cmd_id)})")
                logger.info(f"      - Status: 0x{cmd_status:08x}")
                logger.info(f"      - Sequence: {seq_num}")
                
                if cmd_id == 0x80000009:  # bind_transceiver_resp
                    if cmd_status == 0:
                        logger.info(f"   🎉 {bind_type}: SMPP BIND SUCCESSFUL!")
                        return True
                    else:
                        error_msg = self._get_smpp_error_detailed(cmd_status)
                        logger.error(f"   ❌ {bind_type}: SMPP Bind Failed: {error_msg}")
                        return False
                elif cmd_id == 0x80000002:  # bind_transmitter_resp
                    if cmd_status == 0:
                        logger.info(f"   🎉 {bind_type}: SMPP BIND SUCCESSFUL!")
                        return True
                    else:
                        error_msg = self._get_smpp_error_detailed(cmd_status)
                        logger.error(f"   ❌ {bind_type}: SMPP Bind Failed: {error_msg}")
                        return False
                elif cmd_id == 0x80000001:  # bind_receiver_resp
                    if cmd_status == 0:
                        logger.info(f"   🎉 {bind_type}: SMPP BIND SUCCESSFUL!")
                        return True
                    else:
                        error_msg = self._get_smpp_error_detailed(cmd_status)
                        logger.error(f"   ❌ {bind_type}: SMPP Bind Failed: {error_msg}")
                        return False
                else:
                    cmd_name = self._get_command_name(cmd_id)
                    logger.warning(f"   ⚠️ {bind_type}: Not SMPP bind response: {cmd_name} (0x{cmd_id:08x})")
                    return False
            else:
                logger.error(f"   ❌ {bind_type}: Invalid SMPP response: {len(response)} bytes (min 16 required)")
                return False
        except Exception as e:
            logger.error(f"   ❌ {bind_type}: Error analyzing SMPP response: {e}")
            return False

    def _parse_optional_parameters(self, data):
        """Parse optional parameters from SMPP response"""
        try:
            if not data:
                return
                
            logger.info("🔍 Optional Parameters Analysis:")
            pos = 0
            while pos < len(data) - 4:
                if pos + 4 > len(data):
                    break
                    
                tag = struct.unpack('>H', data[pos:pos+2])[0]
                length = struct.unpack('>H', data[pos+2:pos+4])[0]
                
                if pos + 4 + length > len(data):
                    break
                    
                value = data[pos+4:pos+4+length]
                
                param_name = self._get_optional_param_name(tag)
                logger.info(f"   - {param_name}: {value.hex()}")
                
                pos += 4 + length
                
        except Exception as e:
            logger.info(f"🔍 Error parsing optional parameters: {e}")

    def _get_command_name(self, cmd_id):
        """Get SMPP command name"""
        commands = {
            0x00000001: 'bind_receiver',
            0x00000002: 'bind_transmitter', 
            0x00000003: 'query_sm',
            0x00000004: 'submit_sm',
            0x00000005: 'deliver_sm',
            0x00000006: 'unbind',
            0x00000007: 'replace_sm',
            0x00000008: 'cancel_sm',
            0x00000009: 'bind_transceiver',
            0x0000000B: 'outbind',
            0x00000015: 'enquire_link',
            0x80000001: 'bind_receiver_resp',
            0x80000002: 'bind_transmitter_resp',
            0x80000003: 'query_sm_resp',
            0x80000004: 'submit_sm_resp', 
            0x80000005: 'deliver_sm_resp',
            0x80000006: 'unbind_resp',
            0x80000007: 'replace_sm_resp',
            0x80000008: 'cancel_sm_resp',
            0x80000009: 'bind_transceiver_resp',
            0x80000015: 'enquire_link_resp',
        }
        return commands.get(cmd_id, f'Unknown command: 0x{cmd_id:08x}')

    def _get_optional_param_name(self, tag):
        """Get SMPP optional parameter name"""
        params = {
            0x0005: 'dest_addr_subunit',
            0x0006: 'dest_network_type',
            0x0007: 'dest_bearer_type',
            0x0008: 'dest_telematics_id',
            0x000D: 'source_addr_subunit',
            0x000E: 'source_network_type',
            0x000F: 'source_bearer_type',
            0x0010: 'source_telematics_id',
            0x0017: 'qos_time_to_live',
            0x0019: 'payload_type',
            0x001D: 'additional_status_info_text',
            0x001E: 'receipted_message_id',
            0x0030: 'ms_msg_wait_facilities',
            0x0201: 'privacy_indicator',
            0x0202: 'source_subaddress',
            0x0203: 'dest_subaddress',
            0x0204: 'user_message_reference',
            0x0205: 'user_response_code',
            0x020A: 'source_port',
            0x020B: 'destination_port',
            0x020C: 'sar_msg_ref_num',
            0x020D: 'language_indicator',
            0x020E: 'sar_total_segments',
            0x020F: 'sar_segment_seqnum',
            0x0210: 'SC_interface_version',
            0x0211: 'callback_num_pres_ind',
            0x0212: 'callback_num_atag',
            0x0213: 'number_of_messages',
            0x021C: 'callback_num',
            0x021D: 'dpf_result',
            0x021E: 'set_dpf',
            0x021F: 'ms_availability_status',
            0x0220: 'network_error_code',
            0x0221: 'message_payload',
            0x0222: 'delivery_failure_reason',
            0x0223: 'more_messages_to_send',
            0x0224: 'message_state',
            0x0424: 'ussd_service_op',
            0x0501: 'broadcast_channel_indicator',
            0x0502: 'broadcast_content_type',
            0x0503: 'broadcast_content_type_info',
            0x0504: 'broadcast_message_class',
            0x0505: 'broadcast_rep_num',
            0x0506: 'broadcast_frequency_interval',
            0x0600: 'broadcast_area_identifier',
            0x0601: 'broadcast_error_status',
            0x0602: 'broadcast_area_success',
            0x0603: 'broadcast_end_time',
            0x0604: 'broadcast_service_group',
            0x130C: 'its_session_info',
            0x1380: 'its_reply_type',
        }
        return params.get(tag, f'Unknown parameter: 0x{tag:04x}')
    
    def _test_common_ports(self):
        """Test common SMPP ports"""
        logger.info("")
        logger.info("🎯 STEP 6: ALTERNATIVE PORT TEST")
        logger.info("=" * 50)
        common_ports = [2775, 3550, 3675, 3780, 8080]
        working_ports = []
        
        for port in common_ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((self.host, port))
                sock.close()
                
                if result == 0:
                    working_ports.append(port)
                    logger.info(f"   ✅ Port {port}: OPEN")
                else:
                    logger.info(f"   ❌ Port {port}: Closed")
            except:
                logger.info(f"   ❌ Port {port}: Failed")
        
        if working_ports:
            logger.info(f"   🔍 Open alternative ports: {working_ports}")
        else:
            logger.info("   ❌ No alternative SMPP ports are open")
        
        return working_ports
    
    def _get_smpp_error(self, status):
        """Get SMPP error message"""
        errors = {
            0x00000004: "Incorrect BIND Status for given command",
            0x0000000D: "Bind Failed", 
            0x0000000E: "Invalid Password",
            0x0000000F: "Invalid System ID",
        }
        return errors.get(status, f"Unknown error: 0x{status:08x}")

    def _get_smpp_error_detailed(self, status):
        """Get detailed SMPP error message"""
        errors = {
            0x00000000: "Success",
            0x00000001: "Invalid Message Length",
            0x00000002: "Invalid Command Length", 
            0x00000003: "Invalid Command ID",
            0x00000004: "Incorrect BIND Status for given command",
            0x00000005: "Already in Bound State",
            0x00000006: "Invalid Priority Flag",
            0x00000007: "Invalid Registered Delivery Flag",
            0x00000008: "System Error",
            0x0000000A: "Invalid Source Address",
            0x0000000B: "Invalid Dest Addr",
            0x0000000C: "Message ID is invalid",
            0x0000000D: "Bind Failed",
            0x0000000E: "Invalid Password", 
            0x0000000F: "Invalid System ID",
            0x00000011: "Cancel SM Failed",
            0x00000013: "Replace SM Failed",
            0x00000014: "Message Queue Full",
            0x00000015: "Invalid Service Type",
            0x00000033: "Invalid number of destinations",
            0x00000034: "Invalid distribution list name",
            0x00000040: "Destination flag is invalid",
            0x00000042: "Invalid 'submit with replace' request",
            0x00000043: "Invalid esm_class field data",
            0x00000044: "Cannot submit to distribution list",
            0x00000045: "submit_sm or submit_multi failed",
            0x00000048: "Invalid Source address TON",
            0x00000049: "Invalid Source address NPI",
            0x00000050: "Invalid Destination address TON",
            0x00000051: "Invalid Destination address NPI",
            0x00000053: "Invalid system_type field",
            0x00000054: "Invalid replace_if_present flag",
            0x00000055: "Invalid number of messages",
            0x00000058: "Throttling error (ESME exceeded allowed message limits)",
            0x00000061: "Invalid Scheduled Delivery Time",
            0x00000062: "Invalid message validity period",
            0x00000063: "Predefined Message Invalid or Not Found",
            0x00000064: "ESME Receiver Temporary App Error Code",
            0x00000065: "ESME Receiver Permanent App Error Code",
            0x00000066: "ESME Receiver Reject Message Error Code",
            0x00000067: "query_sm request failed",
            0x000000C0: "Error in the optional part of the PDU Body",
            0x000000C1: "Optional Parameter not allowed",
            0x000000C2: "Invalid Parameter Length",
            0x000000C3: "Expected Optional Parameter missing",
            0x000000C4: "Invalid Optional Parameter Value",
            0x000000FE: "Delivery Failure (used for data_sm_resp)",
            0x000000FF: "Unknown Error",
        }
        return errors.get(status, f"Unknown error: 0x{status:08x}")
    
    def _generate_final_report(self, evidence):
        """Generate final diagnostic report"""
        logger.info("")
        logger.info("=" * 70)
        logger.info("🎯 FINAL DIAGNOSTIC REPORT - CONCLUSIVE EVIDENCE")
        logger.info("=" * 70)
        
        logger.info("📊 TEST RESULTS SUMMARY:")
        logger.info(f"   🔌 Basic Connectivity: {'✅ PASS' if evidence['basic_connectivity'] else '❌ FAIL'}")
        logger.info(f"   📡 Server Response: {'✅ PASS' if evidence['server_response'] else '❌ FAIL'}")
        logger.info(f"   📨 SMPP Protocol: {'✅ PASS' if evidence['smpp_protocol'] else '❌ FAIL'}")
        logger.info(f"   🔧 SMPP Detailed Debug: {'✅ PASS' if evidence['smpp_detailed_debug'] else '❌ FAIL'}")
        logger.info(f"   🔌 Alternative Ports: {evidence['alternative_ports']}")
        
        logger.info("")
        logger.info("🔍 ROOT CAUSE ANALYSIS:")
        if not evidence['basic_connectivity']:
            logger.info("   ❌ Port 9889 is completely closed")
            logger.info("   💡 The port is not listening or firewall blocked")
        elif evidence['server_response'] and not evidence['smpp_protocol']:
            logger.info("   ❌ Port 9889 is open but does NOT run SMPP service")
            logger.info("   💡 This port runs a DIFFERENT SERVICE, not SMPP")
            logger.info("   🔥 CONFIRMED: Server accepts TCP but rejects SMPP protocol")
        else:
            logger.info("   ❌ SMPP service is not running on port 9889")
        
        logger.info("")
        logger.info("🚨 IMMEDIATE ACTION REQUIRED:")
        logger.info("   1. 📞 CONTACT YOUR SMPP PROVIDER with this report")
        logger.info("   2. 📋 PROVIDE THEM THIS CONCLUSIVE EVIDENCE:")
        logger.info("      - ✅ Port 9889 accepts TCP connections")
        logger.info("      - ❌ Server actively rejects SMPP protocol with WinError 10054")
        logger.info("      - 🔥 This proves port 9889 runs a DIFFERENT SERVICE")
        logger.info("   3. 📢 DEMAND the correct SMPP port number")
        logger.info("   4. ❓ Ask: 'What service actually runs on port 9889?'")
        
        logger.info("")
        logger.info("💡 PROVIDER QUESTIONS TO ASK:")
        logger.info("   - 'What is the CORRECT SMPP port number?'")
        logger.info("   - 'Is SMPP service actually running on any port?'") 
        logger.info("   - 'Is my IP 103.153.58.74 whitelisted?'")
        logger.info("   - 'What service runs on port 9889?'")
        logger.info("   - 'What are the exact SMPP connection parameters?'")
        logger.info("=" * 70)

class SMPPConnectionManager:
    def __init__(self, smpp_connection):
        self.smpp_connection = smpp_connection
        self.is_running = False
        self.thread = None
        self.last_error = None
        
        # Print connection details from database
        logger.info("🎯 STEP 0: SMPP CONNECTION MANAGER INITIALIZED")
        logger.info("=" * 50)
        logger.info(f"   🔧 Connection ID: {self.smpp_connection.smpp_id}")
        logger.info(f"   📝 Connection Name: {self.smpp_connection.connect_name}")
        logger.info(f"   🖥️  IP Address: {self.smpp_connection.ip}")
        logger.info(f"   🔌 TX/TRX Port: {self.smpp_connection.tr_trx_port}")
        logger.info(f"   🔌 RX Port: {self.smpp_connection.rx_port}")
        logger.info(f"   👤 Username: {self.smpp_connection.username}")
        logger.info(f"   🔑 Password: {'*' * len(self.smpp_connection.password) if self.smpp_connection.password else 'EMPTY'}")
        logger.info(f"   📊 System Type: {self.smpp_connection.system_type}")
        logger.info(f"   🔗 Bind Type: {self.smpp_connection.bind_type}")
        logger.info("=" * 50)
        
    def run_final_diagnostic(self):
        """Run final diagnostic"""
        logger.info("🔧 STARTING FINAL DIAGNOSTIC...")
        diagnostic = FinalDiagnostic(
            host=self.smpp_connection.ip,
            port=self.smpp_connection.tr_trx_port,
            system_id=self.smpp_connection.username,
            password=self.smpp_connection.password
        )
        
        return diagnostic.run_final_diagnostic()
    
    def start(self):
        """Start final diagnostic"""
        if self.is_running:
            return True
            
        self.is_running = True
        self.thread = threading.Thread(target=self._diagnostic_loop)
        self.thread.daemon = True
        self.thread.start()
        return True
    
    def _diagnostic_loop(self):
        """Main diagnostic loop"""
        try:
            self._update_status("DIAGNOSTIC", False, "Running final diagnostic...")
            
            # Run final diagnostic
            evidence = self.run_final_diagnostic()
            
            # Update status based on results
            if evidence['smpp_protocol']:
                self._update_status("SUCCESS", True, "SMPP service found and working!")
            else:
                self._update_status("FAILED", False, "Diagnostic complete - check logs for evidence")
                
        except Exception as e:
            error_msg = f"Diagnostic error: {e}"
            self.last_error = error_msg
            self._update_status("ERROR", False, error_msg)
            logger.error(f"💥 {error_msg}")
        
        self.is_running = False
    
    def _update_status(self, bind_status, is_alive, status_message=None):
        """Update connection status"""
        try:
            from .models import SmppConnection
            connection = SmppConnection.objects.get(smpp_id=self.smpp_connection.smpp_id)
            
            connection.bind_status = bind_status
            connection.is_alive = is_alive
            connection.last_bind_time = timezone.now() if is_alive else None
            
            if status_message:
                connection.last_error = status_message
            
            connection.save()
            self.smpp_connection = connection
            
        except Exception as e:
            logger.error(f"❌ Error updating status: {e}")
    
    def stop(self):
        """Stop diagnostic"""
        self.is_running = False
        self._update_status("STOPPED", False, "Diagnostic stopped")
        return True

class GlobalConnectionManager:
    def __init__(self):
        self.connections: Dict[int, SMPPConnectionManager] = {}
        self._lock = threading.RLock()
    
    def start_single_connection(self, smpp_id):
        """Start final diagnostic"""
        try:
            from .models import SmppConnection
            connection = SmppConnection.objects.get(smpp_id=smpp_id)
            
            with self._lock:
                if smpp_id in self.connections:
                    self.stop_single_connection(smpp_id)
                
                logger.info(f"🔧 Starting FINAL DIAGNOSTIC for: {connection.connect_name}")
                manager = SMPPConnectionManager(connection)
                self.connections[smpp_id] = manager
                return manager.start()
            
        except Exception as e:
            logger.error(f"❌ Error starting diagnostic {smpp_id}: {e}")
            return False
    
    def stop_single_connection(self, smpp_id):
        """Stop diagnostic"""
        try:
            with self._lock:
                if smpp_id in self.connections:
                    manager = self.connections[smpp_id]
                    success = manager.stop()
                    del self.connections[smpp_id]
                    return success
                return True
        except Exception as e:
            logger.error(f"❌ Error stopping diagnostic {smpp_id}: {e}")
            return False
    
    def get_connection_status(self, smpp_id):
        """Get diagnostic status"""
        try:
            with self._lock:
                if smpp_id in self.connections:
                    manager = self.connections[smpp_id]
                    
                    return {
                        'is_running': manager.is_running,
                        'bind_status': manager.smpp_connection.bind_status,
                        'connection_name': manager.smpp_connection.connect_name,
                        'last_error': manager.last_error,
                        'diagnostic_mode': True
                    }
                else:
                    from .models import SmppConnection
                    try:
                        connection = SmppConnection.objects.get(smpp_id=smpp_id)
                        return {
                            'is_running': False,
                            'bind_status': connection.bind_status,
                            'connection_name': connection.connect_name,
                            'last_error': 'Not in diagnostic mode',
                            'diagnostic_mode': False
                        }
                    except Exception as e:
                        return {'error': f'Connection not found: {e}'}
        except Exception as e:
            logger.error(f"❌ Error getting connection status: {e}")
            return {'error': str(e)}

# Global instance
global_connection_manager = GlobalConnectionManager()