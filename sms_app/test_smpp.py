import socket
import struct
import time

def test_smpp_connection():
    """Manual test of SMPP connection"""
    print("Testing SMPP connection manually...")
    
    # Connection details
    host = '103.153.58.74'
    port = 9889
    username = 'DEVSMPP'
    password = 'Sms@123#'  # Replace with actual password
    system_type = 'SMPP'
    
    try:
        # Create socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        
        print(f"Connecting to {host}:{port}...")
        sock.connect((host, port))
        print("TCP connection established!")
        
        # Prepare bind transceiver PDU
        command_id = 0x00000009  # bind_transceiver
        sequence_number = 1
        
        system_id_bytes = username.encode('ascii') + b'\x00'
        password_bytes = password.encode('ascii') + b'\x00'
        system_type_bytes = system_type.encode('ascii') + b'\x00'
        interface_version = 0x34
        addr_ton = 0x00
        addr_npi = 0x00
        address_range = b'\x00'
        
        # Calculate PDU length
        pdu_length = (
            16 +  # header
            len(system_id_bytes) +
            len(password_bytes) +
            len(system_type_bytes) +
            4 +   # interface_version + addr_ton + addr_npi + addr_range length
            1     # address_range null terminator
        )
        
        # Build PDU
        pdu = struct.pack('>I', pdu_length)
        pdu += struct.pack('>I', command_id)
        pdu += struct.pack('>I', 0)  # command_status
        pdu += struct.pack('>I', sequence_number)
        pdu += system_id_bytes
        pdu += password_bytes
        pdu += system_type_bytes
        pdu += struct.pack('>B', interface_version)
        pdu += struct.pack('>B', addr_ton)
        pdu += struct.pack('>B', addr_npi)
        pdu += address_range
        
        print(f"Sending bind PDU ({len(pdu)} bytes)...")
        sock.send(pdu)
        
        print("Waiting for response...")
        # Read header first
        header = sock.recv(16)
        print(f"Received header: {len(header)} bytes")
        
        if len(header) >= 16:
            total_length = struct.unpack('>I', header[0:4])[0]
            resp_command_id = struct.unpack('>I', header[4:8])[0]
            resp_status = struct.unpack('>I', header[8:12])[0]
            resp_sequence = struct.unpack('>I', header[12:16])[0]
            
            print(f"Response - Total Length: {total_length}")
            print(f"Command ID: 0x{resp_command_id:08x}")
            print(f"Status: 0x{resp_status:08x}")
            print(f"Sequence: {resp_sequence}")
            
            # Read remaining data if any
            remaining = total_length - 16
            if remaining > 0:
                body = sock.recv(remaining)
                print(f"Response body: {body}")
            
            if resp_status == 0:
                print("✓ Bind successful!")
            else:
                print(f"✗ Bind failed with status: 0x{resp_status:08x}")
        
        sock.close()
        
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    test_smpp_connection()