import threading
import time
import struct
import socket
from datetime import datetime
import logging

logger = logging.getLogger('dlr_listener')

class DLRReportParser:
    def __init__(self):
        self.dlr_reports = []
        self.pending_dlrs = {}
        self.dlr_timeout = 300
        
    def add_pending_dlr(self, message_id, message_info):
        """Add message to pending DLR tracking"""
        self.pending_dlrs[message_id] = {
            **message_info,
            'submit_time': datetime.now(),
            'status': 'SENT',
            'dlr_received': False,
            'last_checked': datetime.now(),
            'retry_count': 0
        }
        logger.info(f" Tracking DLR for message: {message_id}")
        
    def check_dlr_timeouts(self):
        """Check for DLR timeouts and return expired messages"""
        expired_messages = []
        try:
            current_time = datetime.now()
            
            for message_id, dlr_info in self.pending_dlrs.items():
                time_diff = (current_time - dlr_info['submit_time']).total_seconds()
                
                if time_diff > self.dlr_timeout and not dlr_info['dlr_received']:
                    logger.warning(f" DLR timeout for message: {message_id} ({time_diff:.0f}s)")
                    expired_messages.append({
                        'message_id': message_id,
                        'status': 'EXPIRED',
                        'reason': 'DLR_TIMEOUT',
                        'submit_date': dlr_info['submit_time'].isoformat(),
                        'done_date': current_time.isoformat(),
                        'error_code': '001'
                    })
                    # Mark as received to avoid duplicate reporting
                    dlr_info['dlr_received'] = True
            
            # Remove expired messages after processing
            for expired in expired_messages:
                if expired['message_id'] in self.pending_dlrs:
                    del self.pending_dlrs[expired['message_id']]
                    
        except Exception as e:
            logger.error(f"Error in DLR timeout checker: {str(e)}")
        
        return expired_messages
    
    def update_pending_dlr(self, message_id, status):
        """Update pending DLR status"""
        if message_id in self.pending_dlrs:
            self.pending_dlrs[message_id]['dlr_received'] = True
            self.pending_dlrs[message_id]['status'] = status
            self.pending_dlrs[message_id]['last_checked'] = datetime.now()
            logger.info(f" Updated pending DLR: {message_id} -> {status}")
        
    def parse_dlr_content(self, dlr_content, message_id=None):
        """Parse DLR content and extract all required fields for report"""
        try:
            logger.info(" Parsing DLR content for comprehensive report...")
            
            # Extract actual values from DLR content
            actual_sender = "N/A"
            actual_receiver = "N/A"
            actual_content = "N/A"
            
            # Extract from DLR content if available
            if 'from:' in dlr_content:
                start = dlr_content.find('from:') + 5
                end = dlr_content.find(' ', start)
                actual_sender = dlr_content[start:end] if end != -1 else dlr_content[start:]
                
            if 'to:' in dlr_content:
                start = dlr_content.find('to:') + 3
                end = dlr_content.find(' ', start)
                actual_receiver = dlr_content[start:end] if end != -1 else dlr_content[start:]
            
            report = {
                'sender': actual_sender if actual_sender != "N/A" else 'VDSLON',
                'receiver': actual_receiver if actual_receiver != "N/A" else '9181020701215',
                'content': actual_content if actual_content != "N/A" else 'The OTP for your Change password Request is 123456 VD STRIDE MEDIA',
                'submit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'dlr_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'message_id': message_id or 'N/A',
                'account': 'VDSLON',
                'parts': '1',
                'entity_id': '1501404380000052845',
                'content_id': '1707172898474018613',
                'campaign': 'OTP_Campaign',
                'status': 'UNKNOWN',
                'reason': 'N/A',
                'encoding': 'GSM',
                'smsc': 'SMSC_Provider',
                'masked_reason': 'N/A',
                'masked_status': 'N/A',
                'tmid': '1207161893755246802',
                'raw_dlr': dlr_content,
                'processing_time': datetime.now().isoformat()
            }
            
            # Extract all possible fields from DLR content
            self._extract_dlr_fields(report, dlr_content)
            
            # Update pending DLR if exists
            if message_id and message_id != 'N/A':
                self.update_pending_dlr(message_id, report['status'])
            
            # Add to reports history
            self.dlr_reports.append(report.copy())
            
            logger.info(" DLR Report parsed successfully")
            return report
            
        except Exception as e:
            logger.error(f" Error parsing DLR report: {str(e)}")
            return None
    
    def _extract_dlr_fields(self, report, dlr_content):
        """Extract all fields from DLR content"""
        try:
            fields_to_extract = {
                'id': 'message_id',
                'sub': 'submitted_parts', 
                'dlvrd': 'delivered_parts',
                'submit date': 'submit_time',
                'done date': 'dlr_time',
                'stat': 'status',
                'err': 'reason',
                'text': 'content'
            }
            
            for field, report_key in fields_to_extract.items():
                if f'{field}:' in dlr_content:
                    start = dlr_content.find(f'{field}:') + len(field) + 1
                    end = dlr_content.find(' ', start)
                    value = dlr_content[start:end] if end != -1 else dlr_content[start:]
                    
                    if value and value != 'null':
                        if report_key == 'message_id' and report['message_id'] == 'N/A':
                            report[report_key] = value
                        elif report_key == 'status':
                            report[report_key] = value
                            report['masked_status'] = self._mask_status(value)
                        elif report_key == 'reason':
                            report[report_key] = value
                            report['masked_reason'] = self._mask_reason(value)
                        elif report_key in ['submit_time', 'dlr_time']:
                            formatted_date = self._parse_smpp_date(value)
                            if formatted_date:
                                report[report_key] = formatted_date
                        elif report_key == 'submitted_parts':
                            report['parts'] = value
                        elif report_key == 'content' and value:
                            report[report_key] = value
            
            self._extract_additional_info(report, dlr_content)
            
        except Exception as e:
            logger.error(f" Error extracting DLR fields: {str(e)}")
    
    def _extract_additional_info(self, report, dlr_content):
        """Extract additional information from DLR content"""
        try:
            if 'to:' in dlr_content:
                start = dlr_content.find('to:') + 3
                end = dlr_content.find(' ', start)
                receiver = dlr_content[start:end] if end != -1 else dlr_content[start:]
                if receiver and receiver != 'null':
                    report['receiver'] = receiver
            
            if 'from:' in dlr_content:
                start = dlr_content.find('from:') + 5
                end = dlr_content.find(' ', start)
                sender = dlr_content[start:end] if end != -1 else dlr_content[start:]
                if sender and sender != 'null':
                    report['sender'] = sender
            
            if 'smsc:' in dlr_content:
                start = dlr_content.find('smsc:') + 5
                end = dlr_content.find(' ', start)
                smsc = dlr_content[start:end] if end != -1 else dlr_content[start:]
                if smsc and smsc != 'null':
                    report['smsc'] = smsc
            
            if 'account:' in dlr_content:
                start = dlr_content.find('account:') + 8
                end = dlr_content.find(' ', start)
                account = dlr_content[start:end] if end != -1 else dlr_content[start:]
                if account and account != 'null':
                    report['account'] = account
                    
        except Exception as e:
            logger.error(f" Error extracting additional info: {str(e)}")
    
    def _parse_smpp_date(self, smpp_date):
        """Convert SMPP date format to readable format"""
        try:
            if not smpp_date or smpp_date == 'N/A' or smpp_date == 'null':
                return None
            
            if len(smpp_date) >= 10:
                year = "20" + smpp_date[0:2]
                month = smpp_date[2:4]
                day = smpp_date[4:6]
                hour = smpp_date[6:8]
                minute = smpp_date[8:10]
                second = smpp_date[10:12] if len(smpp_date) >= 12 else "00"
                
                return f"{year}-{month}-{day} {hour}:{minute}:{second}"
            else:
                return None
                
        except Exception as e:
            logger.error(f" Error parsing SMPP date: {str(e)}")
            return None
    
    def _mask_status(self, status):
        """Mask status for reporting"""
        status_mapping = {
            'DELIVRD': 'DELIVERED',
            'EXPIRED': 'EXPIRED', 
            'DELETED': 'DELETED',
            'UNDELIV': 'UNDELIVERABLE',
            'ACCEPTD': 'ACCEPTED',
            'UNKNOWN': 'UNKNOWN',
            'REJECTD': 'REJECTED'
        }
        return status_mapping.get(status, status)
    
    def _mask_reason(self, reason):
        """Mask reason code for reporting"""
        reason_mapping = {
            '000': 'SUCCESS',
            '001': 'MESSAGE_EXPIRED',
            '002': 'DELETED_BY_OPERATOR',
            '003': 'UNDELIVERABLE_MESSAGE',
            '004': 'UNKNOWN_SUBSCRIBER',
            '005': 'SUBSCRIBER_BARRED',
            '006': 'INSUFFICIENT_BALANCE',
            '007': 'UNKNOWN_ERROR',
            '008': 'INVALID_DESTINATION',
            '009': 'ROUTING_ERROR',
            '010': 'SYSTEM_ERROR'
        }
        return reason_mapping.get(reason, f"ERROR_{reason}")
    
    def generate_detailed_report(self, report):
        """Generate detailed human-readable report"""
        try:
            detailed_report = []
            detailed_report.append("=" * 80)
            detailed_report.append(" COMPREHENSIVE DLR REPORT")
            detailed_report.append("=" * 80)
            
            detailed_report.append(" BASIC INFORMATION:")
            detailed_report.append(f"  Message ID:      {report.get('message_id', 'N/A')}")
            detailed_report.append(f"  Sender:          {report.get('sender', 'N/A')}")
            detailed_report.append(f"  Receiver:        {report.get('receiver', 'N/A')}")
            detailed_report.append(f"  Account:         {report.get('account', 'N/A')}")
            
            detailed_report.append("\n TIMING INFORMATION:")
            detailed_report.append(f"  Submit Time:     {report.get('submit_time', 'N/A')}")
            detailed_report.append(f"  DLR Time:        {report.get('dlr_time', 'N/A')}")
            detailed_report.append(f"  Processed:       {report.get('processing_time', 'N/A')}")
            
            detailed_report.append("\n DLT INFORMATION:")
            detailed_report.append(f"  Entity ID:       {report.get('entity_id', 'N/A')}")
            detailed_report.append(f"  Content ID:      {report.get('content_id', 'N/A')}")
            detailed_report.append(f"  TMID:            {report.get('tmid', 'N/A')}")
            detailed_report.append(f"  Campaign:        {report.get('campaign', 'N/A')}")
            
            detailed_report.append("\n DELIVERY INFORMATION:")
            detailed_report.append(f"  Status:          {report.get('status', 'N/A')}")
            detailed_report.append(f"  Masked Status:   {report.get('masked_status', 'N/A')}")
            detailed_report.append(f"  Reason:          {report.get('reason', 'N/A')}")
            detailed_report.append(f"  Masked Reason:   {report.get('masked_reason', 'N/A')}")
            detailed_report.append(f"  Parts:           {report.get('parts', 'N/A')}")
            
            detailed_report.append("\n TECHNICAL INFORMATION:")
            detailed_report.append(f"  Encoding:        {report.get('encoding', 'N/A')}")
            detailed_report.append(f"  SMSC:            {report.get('smsc', 'N/A')}")
            
            detailed_report.append("\n RAW DLR CONTENT:")
            raw_content = report.get('raw_dlr', 'N/A')
            detailed_report.append(f"  Raw:             {raw_content}")
            
            detailed_report.append("=" * 80)
            
            return '\n'.join(detailed_report)
            
        except Exception as e:
            logger.error(f" Error generating detailed report: {str(e)}")
            return f"Error generating detailed report: {str(e)}"

    def get_all_reports(self):
        return self.dlr_reports
    
    def get_pending_dlrs(self):
        return self.pending_dlrs
    
    def get_report_count(self):
        return len(self.dlr_reports)
    
    def get_pending_count(self):
        return len(self.pending_dlrs)
    
    def clear_reports(self):
        self.dlr_reports.clear()
    
    def clear_pending_dlrs(self):
        self.pending_dlrs.clear()
    
    def get_latest_report(self):
        if self.dlr_reports:
            return self.dlr_reports[-1]
        return None


class DLRListener:
    def __init__(self, smpp_client, dlr_callback=None):
        self.smpp_client = smpp_client
        self.listening = False
        self.dlr_thread = None
        self.timeout_thread = None
        self.report_parser = DLRReportParser()
        self.dlr_callback = dlr_callback
        self.logger = logger
        self.last_activity = datetime.now()
        self.buffer = b''  # Buffer for incomplete PDUs
        
    def start_listening(self):
        """Start DLR listener in background thread"""
        if self.listening:
            self.logger.info("DLR listener already running")
            return
            
        self.listening = True
        self.dlr_thread = threading.Thread(target=self._listen_for_dlr, daemon=True)
        self.dlr_thread.start()
        
        self.timeout_thread = threading.Thread(target=self._check_timeouts, daemon=True)
        self.timeout_thread.start()
        
        self.logger.info("🚀 DLR Listener started with buffer management")
        
    def stop_listening(self):
        """Stop DLR listener"""
        self.listening = False
        if self.dlr_thread:
            self.dlr_thread.join(timeout=5)
        if self.timeout_thread:
            self.timeout_thread.join(timeout=5)
        self.logger.info("🛑 DLR Listener stopped")
        
    def _check_timeouts(self):
        """Check for DLR timeouts in background"""
        while self.listening:
            try:
                expired_messages = self.report_parser.check_dlr_timeouts()
                
                # Process expired messages through callback
                for expired in expired_messages:
                    if self.dlr_callback:
                        try:
                            expired_data = {
                                'message_id': expired['message_id'],
                                'status': expired['status'],
                                'reason': expired['reason'],
                                'submit_date': expired['submit_date'],
                                'done_date': expired['done_date'],
                                'error_code': expired['error_code'],
                                'raw_content': 'DLR_TIMEOUT',
                                'source_addr': 'N/A',
                                'destination_addr': 'N/A',
                                'timestamp': datetime.now().isoformat()
                            }
                            self.dlr_callback(expired_data)
                            self.logger.info(f"⏰ DLR Timeout Callback: {expired['message_id']}")
                        except Exception as e:
                            self.logger.error(f"❌ Error in timeout callback: {str(e)}")
                
                time.sleep(30)
            except Exception as e:
                self.logger.error(f"❌ Error in timeout checker: {str(e)}")
                time.sleep(60)
        
    def add_pending_dlr(self, message_id, message_info):
        """Add message to pending DLR tracking"""
        self.report_parser.add_pending_dlr(message_id, message_info)
        
    def get_dlr_reports(self):
        return self.report_parser.get_all_reports()
    
    def get_pending_dlrs(self):
        return self.report_parser.get_pending_dlrs()
        
    def get_latest_dlr_report(self):
        return self.report_parser.get_latest_report()
        
    def clear_dlr_reports(self):
        self.report_parser.clear_reports()
        
    def clear_pending_dlrs(self):
        self.report_parser.clear_pending_dlrs()
        
    def set_dlr_callback(self, callback):
        self.dlr_callback = callback
        self.logger.info("✅ DLR callback function set")
        
    def get_stats(self):
        return {
            'total_reports': self.report_parser.get_report_count(),
            'pending_dlrs': self.report_parser.get_pending_count(),
            'listening': self.listening,
            'connected': self.smpp_client.connected if self.smpp_client else False,
            'last_activity': self.last_activity.isoformat(),
            'buffer_size': len(self.buffer)
        }
        
    def _listen_for_dlr(self):
        """Listen for delivery reports - WITH BUFFER MANAGEMENT"""
        self.logger.info("📡 DLR Listener thread started - BUFFERED MODE")
        
        while self.listening:
            try:
                # Check if client is connected
                if not self.smpp_client or not self.smpp_client.connected:
                    self.logger.warning("❌ SMPP client not connected, waiting...")
                    time.sleep(2)
                    continue
                
                # Check socket
                if not self.smpp_client.socket:
                    self.logger.warning("❌ No socket available, waiting...")
                    time.sleep(2)
                    continue
                
                # Set timeout for real-time processing
                self.smpp_client.socket.settimeout(2.0)
                
                try:
                    # Try to receive data
                    data = self.smpp_client.socket.recv(8192)
                    
                    if data:
                        self.last_activity = datetime.now()
                        self.logger.info(f"📨 RAW DATA RECEIVED: {len(data)} bytes")
                        
                        # Process the data with buffer management
                        self.process_incoming_data(data)
                    else:
                        self.logger.debug("🔇 No data received (empty)")
                        
                except socket.timeout:
                    # No data received, continue listening
                    continue
                except Exception as e:
                    self.logger.error(f"💥 Socket receive error: {str(e)}")
                    if self.listening:
                        time.sleep(2)
                    break
                    
            except Exception as e:
                self.logger.error(f"💥 DLR listener main loop error: {str(e)}")
                time.sleep(5)
                
        self.logger.info("🛑 DLR Listener thread stopped")
    
    def process_incoming_data(self, data):
        """Process incoming data with buffer management"""
        try:
            # Add new data to buffer
            self.buffer += data
            self.logger.info(f"📦 Buffer size: {len(self.buffer)} bytes")
            
            # Process complete PDUs from buffer
            while len(self.buffer) >= 4:  # Minimum header size check
                # Try to parse PDU header to get expected length
                try:
                    if len(self.buffer) < 16:
                        # Not enough data for full header, wait for more
                        self.logger.debug(f"⏳ Waiting for more data: have {len(self.buffer)} bytes, need 16 for header")
                        break
                    
                    # Parse PDU header
                    command_length, command_id, command_status, sequence_number = struct.unpack('>IIII', self.buffer[:16])
                    
                    # Check if we have a complete PDU
                    if len(self.buffer) >= command_length:
                        # Extract complete PDU
                        complete_pdu = self.buffer[:command_length]
                        self.buffer = self.buffer[command_length:]  # Remove processed data
                        
                        self.logger.info(f"✅ Processing complete PDU: {command_length} bytes")
                        self.process_complete_pdu(complete_pdu, command_length, command_id, command_status, sequence_number)
                    else:
                        # Incomplete PDU, wait for more data
                        self.logger.info(f"⏳ Incomplete PDU: have {len(self.buffer)} bytes, need {command_length}")
                        break
                        
                except struct.error as e:
                    self.logger.error(f"💥 Failed to parse PDU header: {str(e)}")
                    # Clear buffer on parse error to avoid infinite loop
                    self.buffer = b''
                    break
                except Exception as e:
                    self.logger.error(f"💥 Error processing buffer: {str(e)}")
                    self.buffer = b''
                    break
                    
        except Exception as e:
            self.logger.error(f"💥 Error in buffered processing: {str(e)}")
            self.buffer = b''  # Reset buffer on error
    
    def process_complete_pdu(self, data, command_length, command_id, command_status, sequence_number):
        """Process a complete PDU"""
        try:
            self.logger.info(f"📦 COMPLETE PDU ANALYSIS:")
            self.logger.info(f"   Command Length: {command_length}")
            self.logger.info(f"   Command ID:     0x{command_id:08x}")
            self.logger.info(f"   Command Status: 0x{command_status:08x}")
            self.logger.info(f"   Sequence Number: {sequence_number}")
            
            # Check if this is a deliver_sm (DLR)
            if command_id == 0x00000005:  # deliver_sm
                self.logger.info("🎯 PROCESSING deliver_sm PDU (DLR)")
                self.parse_deliver_sm_detailed(data, command_length, sequence_number)
            elif command_id == 0x00000015:  # enquire_link
                self.logger.info("🫀 PROCESSING enquire_link PDU")
                self.send_enquire_link_resp(sequence_number)
            elif command_id == 0x80000015:  # enquire_link_resp
                self.logger.info("✅ PROCESSING enquire_link_resp PDU")
            elif command_id == 0x80000005:  # deliver_sm_resp
                self.logger.info("📨 PROCESSING deliver_sm_resp PDU")
            else:
                self.logger.info(f"❓ PROCESSING Other PDU: 0x{command_id:08x}")
                
        except Exception as e:
            self.logger.error(f"💥 Error processing complete PDU: {str(e)}")
            
    def parse_deliver_sm_detailed(self, data, pdu_length, sequence_number):
        """Detailed parsing of deliver_sm PDU for DLR content"""
        try:
            self.logger.info("=" * 60)
            self.logger.info("📬 DELIVERY REPORT RECEIVED - DETAILED PARSING")
            self.logger.info("=" * 60)
            
            offset = 16  # Skip header
            
            # Parse each field with detailed logging
            self.logger.info(" PARSING PDU FIELDS:")
            
            # Service type
            service_type_end = data.find(b'\x00', offset)
            service_type = data[offset:service_type_end].decode('ascii', errors='ignore') if service_type_end != -1 else ""
            offset = service_type_end + 1 if service_type_end != -1 else offset
            self.logger.info(f"   Service Type: '{service_type}'")
            
            # Source TON/NPI
            source_addr_ton = data[offset] if offset < len(data) else 0
            source_addr_npi = data[offset + 1] if offset + 1 < len(data) else 0
            offset += 2
            self.logger.info(f"   Source TON: {source_addr_ton}, NPI: {source_addr_npi}")
            
            # Source address
            source_addr_end = data.find(b'\x00', offset)
            source_addr = data[offset:source_addr_end].decode('ascii', errors='ignore') if source_addr_end != -1 else ""
            offset = source_addr_end + 1 if source_addr_end != -1 else offset
            self.logger.info(f"   Source Addr: '{source_addr}'")
            
            # Dest TON/NPI
            dest_addr_ton = data[offset] if offset < len(data) else 0
            dest_addr_npi = data[offset + 1] if offset + 1 < len(data) else 0
            offset += 2
            self.logger.info(f"   Dest TON: {dest_addr_ton}, NPI: {dest_addr_npi}")
            
            # Destination address
            dest_addr_end = data.find(b'\x00', offset)
            destination_addr = data[offset:dest_addr_end].decode('ascii', errors='ignore') if dest_addr_end != -1 else ""
            offset = dest_addr_end + 1 if dest_addr_end != -1 else offset
            self.logger.info(f"   Dest Addr: '{destination_addr}'")
            
            # Skip other fields to get to message content
            offset += 13
            
            # SM length
            sm_length = data[offset] if offset < len(data) else 0
            offset += 1
            self.logger.info(f"   SM Length: {sm_length}")
            
            # Short message (DLR content)
            dlr_content = ""
            if sm_length > 0 and offset + sm_length <= len(data):
                dlr_content = data[offset:offset + sm_length].decode('ascii', errors='ignore')
                self.logger.info(f"📝 DLR CONTENT: '{dlr_content}'")
            else:
                self.logger.warning("❌ No DLR content found or invalid length")
                
            # Extract DLR data
            dlr_data = self.extract_dlr_data_fast(dlr_content, source_addr, destination_addr)
            
            # Handle DLR
            self.handle_delivery_report_realtime(dlr_data)
            
            # Send response
            self.send_deliver_sm_resp(sequence_number)
            
            self.logger.info("=" * 60)
            
        except Exception as e:
            self.logger.error(f"💥 Error in detailed deliver_sm parsing: {str(e)}")
    
    def extract_dlr_data_fast(self, dlr_content, source_addr, destination_addr):
        """Fast extraction of DLR data from content"""
        dlr_data = {
            'message_id': "N/A",
            'status': 'UNKNOWN',
            'reason': 'N/A',
            'submit_date': 'N/A',
            'done_date': 'N/A',
            'error_code': '000',
            'raw_content': dlr_content,
            'source_addr': source_addr,
            'destination_addr': destination_addr,
            'timestamp': datetime.now().isoformat(),
            'processing_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Quick extraction
        if 'id:' in dlr_content:
            start = dlr_content.find('id:') + 3
            end = dlr_content.find(' ', start)
            dlr_data['message_id'] = dlr_content[start:end] if end != -1 else dlr_content[start:]
            
        if 'stat:' in dlr_content:
            start = dlr_content.find('stat:') + 5
            end = dlr_content.find(' ', start)
            dlr_data['status'] = dlr_content[start:end] if end != -1 else dlr_content[start:]
            
        if 'err:' in dlr_content:
            start = dlr_content.find('err:') + 4
            end = dlr_content.find(' ', start)
            dlr_data['error_code'] = dlr_content[start:end] if end != -1 else dlr_content[start:]
            dlr_data['reason'] = f"Error: {dlr_data['error_code']}"
            
        if 'submit date:' in dlr_content:
            start = dlr_content.find('submit date:') + 12
            end = dlr_content.find(' ', start)
            dlr_data['submit_date'] = dlr_content[start:end] if end != -1 else dlr_content[start:]
            
        if 'done date:' in dlr_content:
            start = dlr_content.find('done date:') + 10
            end = dlr_content.find(' ', start)
            dlr_data['done_date'] = dlr_content[start:end] if end != -1 else dlr_content[start:]
        
        return dlr_data
    
    def handle_delivery_report_realtime(self, dlr_data):
        """Handle incoming DLR in real-time"""
        try:
            message_id = dlr_data.get('message_id')
            status = dlr_data.get('status')
            
            # Status emoji mapping
            status_emoji = {
                'DELIVRD': '✅ DELIVERED',
                'UNDELIV': '❌ UNDELIVERABLE', 
                'EXPIRED': '⏰ EXPIRED',
                'REJECTD': '🚫 REJECTED',
                'ACCEPTD': '📨 ACCEPTED',
                'UNKNOWN': '❓ UNKNOWN',
                'DELETED': '🗑️ DELETED'
            }.get(status, f'❓ {status}')
            
            self.logger.info(f"📨 DLR PROCESSED: {message_id} - {status_emoji}")
            
            # Parse comprehensive report
            dlr_report = self.report_parser.parse_dlr_content(dlr_data['raw_content'], message_id)
            
            if dlr_report:
                # Log comprehensive report
                detailed_report = self.report_parser.generate_detailed_report(dlr_report)
                self.logger.info(f"\n{detailed_report}")
            
            # Call callback function if provided
            if self.dlr_callback and message_id != "N/A":
                try:
                    self.dlr_callback(dlr_data)
                    self.logger.info("✅ DLR callback executed successfully")
                except Exception as e:
                    self.logger.error(f"❌ Error in DLR callback: {str(e)}")
            
        except Exception as e:
            self.logger.error(f"💥 Error handling real-time DLR: {str(e)}")
            
    def send_deliver_sm_resp(self, sequence_number):
        """Send deliver_sm response"""
        try:
            command_length = 16
            command_id = 0x80000005
            command_status = 0x00000000
            
            pdu = struct.pack('>IIII', command_length, command_id, command_status, sequence_number)
            self.smpp_client.socket.send(pdu)
            self.logger.info("📨 Deliver_SM Response sent")
            
        except Exception as e:
            self.logger.error(f"💥 Error sending deliver_sm_resp: {str(e)}")
            
    def send_enquire_link_resp(self, sequence_number):
        """Send enquire_link response"""
        try:
            command_length = 16
            command_id = 0x80000015
            command_status = 0x00000000
            
            pdu = struct.pack('>IIII', command_length, command_id, command_status, sequence_number)
            self.smpp_client.socket.send(pdu)
            self.logger.info("🫀 Enquire Link Response sent")
            
        except Exception as e:
            self.logger.error(f"💥 Error sending enquire_link_resp: {str(e)}")