from celery import shared_task
from sms_app.deliver_sm import handle_deliver_sm

@shared_task
def process_dlr_task(raw_pdu: str):
    """
    Celery task to process deliver_sm DLR.
    """
    handle_deliver_sm(raw_pdu)
    return {"status": "processed"}
