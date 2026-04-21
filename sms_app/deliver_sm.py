# dlr_handler.py
import re
from django.utils import timezone
from django.db import transaction
from .models import ComposeMessageLine
from datetime import datetime

# Map SMPP stat codes to human-readable status
SMPP_STATUS_MAP = {
    "DELIVRD": "DELIVERED",
    "UNDELIV": "UNDELIVERED",
    "REJECTD": "REJECTED",
    "(NULL)": "UNKNOWN",
    "": "UNKNOWN"
}

def parse_smpp_timestamp(smpp_ts: str):
    """Convert SMPP timestamp (YYMMDDhhmmss) to Python datetime"""
    try:
        return datetime.strptime(smpp_ts, "%y%m%d%H%M%S")
    except Exception:
        return timezone.now()

def clean_raw_pdu(raw_pdu: str) -> str:
    """
    Remove any prefix junk before the actual DLR fields.
    Looks for the first occurrence of 'id:' and trims everything before it.
    """
    idx = raw_pdu.find("id:")
    if idx != -1:
        return raw_pdu[idx:]
    return raw_pdu

def parse_dlr_pdu(raw_pdu: str) -> dict:
    """
    Parse SMPP deliver_sm PDU string into a structured dictionary
    with human-readable status and reason.
    """
    raw_pdu = clean_raw_pdu(raw_pdu)
    dlr_data = {}

    # Message ID
    match_id = re.search(r"id[:=]([a-fA-F0-9\-]+)", raw_pdu, re.IGNORECASE)
    dlr_data["message_id"] = match_id.group(1).strip() if match_id else None

    # dlvrd count
    match_dlvrd = re.search(r"dlvrd[:=](\d+)", raw_pdu)
    dlvrd_count = int(match_dlvrd.group(1)) if match_dlvrd else None

    # stat
    match_stat = re.search(r"stat[:=]([A-Za-z0-9_()]+)", raw_pdu, re.IGNORECASE)
    stat_val = match_stat.group(1).strip().upper() if match_stat else "(NULL)"

    # Determine human-readable status
    if stat_val not in ["(NULL)", ""]:
        dlr_data["status"] = SMPP_STATUS_MAP.get(stat_val, stat_val)
    else:
        if dlvrd_count == 0:
            dlr_data["status"] = "UNDELIVERED"
        elif dlvrd_count and dlvrd_count > 0:
            dlr_data["status"] = "DELIVERED"
        else:
            dlr_data["status"] = "UNKNOWN"

    # Reason / error
    match_err = re.search(r"err[:=]([0-9()]+)", raw_pdu)
    err_val = match_err.group(1).strip() if match_err else ""
    dlr_data["reason"] = "" if err_val in ["(null)", "000", ""] else err_val

    # submit / done timestamps
    match_submit = re.search(r"submit date[:=](\d+)", raw_pdu)
    dlr_data["submit_time"] = parse_smpp_timestamp(match_submit.group(1)) if match_submit else None

    match_done = re.search(r"done date[:=](\d+)", raw_pdu)
    dlr_data["dlr_time"] = parse_smpp_timestamp(match_done.group(1)) if match_done else timezone.now()

    # raw JSON for reference
    dlr_data["raw_json"] = {
        "dlr_received": timezone.now().isoformat(),
        "dlr_status": dlr_data["status"],
        "dlr_error": dlr_data["reason"],
        "short_message": raw_pdu[:255]
    }

    return dlr_data

def handle_deliver_sm(raw_bytes: bytes):
    """
    Process deliver_sm PDU bytes and safely update ComposeMessageLine.
    Handles multiple lines if the same message_id exists for multiple recipients.
    """
    try:
        raw_pdu = raw_bytes.decode('latin-1', errors='ignore')
    except Exception:
        raw_pdu = str(raw_bytes)

    dlr_data = parse_dlr_pdu(raw_pdu)
    message_id = dlr_data.get("message_id")

    if not message_id:
        print("❌ No Message ID found in DLR PDU")
        return

    def update_lines():
        # Fetch all lines with this message_id
        lines = ComposeMessageLine.objects.filter(message_id=message_id)
        if not lines.exists():
            print(f"❌ No ComposeMessageLine found for message_id={message_id}")
            return

        for line in lines:
            line.status = dlr_data.get("status", "UNKNOWN")
            line.reason = dlr_data.get("reason", "")
            line.dlr_time = dlr_data.get("dlr_time", timezone.now())
            line.submit_time = dlr_data.get("submit_time")
            line.attributes_5 = str(dlr_data.get("raw_json"))
            line.save()
            print(f"✅ DLR Updated for {message_id}, number {line.mobile_number}: {line.status}")

    transaction.on_commit(update_lines)



# context_processors.py
def role_permissions(request):
    """
    Context processor to add user role permissions to all templates
    Based on the permission matrix provided
    """
    permissions = {
        # All users have access to dashboard
        'can_access_dashboard': True,
        
        # Default permissions (will be overridden based on role)
        'can_create_company': False,
        'can_create_role': False,
        'can_manage_users': False,
        'can_manage_credits': False,
        'can_manage_senders': False,
        'can_manage_templates': False,
        'can_manage_groups': False,
        'can_compose_message': False,
        'can_view_sms_summary': False,
        'can_view_dlr': False,
        'can_manage_smpp': False,
        'can_manage_routes': False,
        
        # Role name for template display
        'user_role': None,
    }
    
    if request.user.is_authenticated:
        # Get user's role from the user model
        # Assuming you have a role field in your User model
        user_role = getattr(request.user, 'role_name', None) or getattr(request.user, 'role', None)
        
        # Store role name for template use
        permissions['user_role'] = user_role
        
        # SUPER USERS (Django superuser) - All permissions
        if request.user.is_superuser:
            for key in permissions:
                if key.startswith('can_'):
                    permissions[key] = True
            permissions['user_role'] = 'Super User'
        
        # SUPER ADMIN role
        elif user_role == 'Super Admin':
            permissions.update({
                'can_create_company': False,  # N in your table
                'can_create_role': False,     # N in your table
                'can_manage_users': True,     # Y
                'can_manage_credits': True,   # Y
                'can_manage_senders': True,   # Y
                'can_manage_templates': True, # Y
                'can_manage_groups': True,    # Y (assuming Y)
                'can_compose_message': True,  # Y (assuming Y)
                'can_view_sms_summary': True, # Y
                'can_view_dlr': True,         # Y
                'can_manage_smpp': False,     # N
                'can_manage_routes': True,    # Y
            })
        
        # SUPPORT ADMIN role
        elif user_role == 'Support Admin':
            permissions.update({
                'can_create_company': False,  # N
                'can_create_role': False,     # N
                'can_manage_users': True,     # Y
                'can_manage_credits': True,   # Y
                'can_manage_senders': True,   # Y
                'can_manage_templates': True, # Y
                'can_manage_groups': False,   # N
                'can_compose_message': False, # N
                'can_view_sms_summary': True, # Y
                'can_view_dlr': True,         # Y
                'can_manage_smpp': False,     # N
                'can_manage_routes': True,    # Y
            })
        
        # RESELLER role
        elif user_role == 'Reseller':
            permissions.update({
                'can_create_company': False,  # N
                'can_create_role': False,     # N
                'can_manage_users': True,     # Y
                'can_manage_credits': True,   # Y
                'can_manage_senders': True,   # Y
                'can_manage_templates': True, # Y
                'can_manage_groups': False,   # N
                'can_compose_message': False, # N
                'can_view_sms_summary': True, # Y
                'can_view_dlr': True,         # Y
                'can_manage_smpp': False,     # N
                'can_manage_routes': False,   # N
            })
        
        # CLIENT role
        elif user_role == 'Client':
            permissions.update({
                'can_create_company': False,  # N
                'can_create_role': False,     # N
                'can_manage_users': False,    # N
                'can_manage_credits': False,  # N
                'can_manage_senders': True,   # Y
                'can_manage_templates': True, # Y
                'can_manage_groups': True,    # Y
                'can_compose_message': True,  # Y
                'can_view_sms_summary': True, # Y
                'can_view_dlr': True,         # Y
                'can_manage_smpp': False,     # N
                'can_manage_routes': False,   # N
            })
    
    return {'permissions': permissions}