import os
import django
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sms_project.settings")
django.setup()

from sms_app.models import SmppConnection
from sms_app.smpp_manager import SMPPSessionManager
from sms_app.smpp_dlr_manager import DLRListener

def start_worker():
    print("🚀 SMPP Background Worker Started")

    active = SmppConnection.objects.filter(
        bind_status="BOUND",
        is_alive=True
    )

    if not active.exists():
        print("❌ No SMPP connections available")
        return

    for conn in active:
        print(f"🔌 Starting SMPP session for: {conn.smpp_id}")
        session = SMPPSessionManager.start_session(conn.smpp_id)

        if session:
            listener = DLRListener(session)
            listener.daemon = True
            listener.start()
            print(f"📡 DLR Listener running for SMPP {conn.smpp_id}")

    # Keep worker alive forever
    while True:
        time.sleep(2)

if __name__ == "__main__":
    start_worker()
