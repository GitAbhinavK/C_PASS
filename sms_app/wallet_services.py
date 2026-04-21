# # yourapp/services/wallet_services.py
# from decimal import Decimal
# import logging

# from django.db import transaction
# from django.utils import timezone

# from sms_app.models import Wallet, Credit, User  # adjust import path as needed

# log = logging.getLogger("wallet_service")

# class WalletService:
#     """
#     Wallet & Credit management service.
#     - Respects wallet.plan_type (Prepaid / Postpaid)
#     - Respects wallet.deduction_type (SUBMISSION / DELIVERED)
#     - Creates ledger entries in Credit
#     """

#     @staticmethod
#     def get_user_wallet(user):
#         """Return the latest wallet for the user or None."""
#         if not user:
#             return None
#         return Wallet.objects.filter(user=user).order_by('-wallet_id').first()

#     @staticmethod
#     def _create_credit_entry(
#         user,
#         wallet,
#         *,
#         action_type,
#         transaction_type,
#         status,
#         reference_id,
#         used_credit,
#         amount,
#         starting_balance,
#         ending_balance,
#         comments=None,
#         description=None,
#         attributes=None
#     ):
#         """
#         Create & return a Credit ledger row.
#         Note: Credit model's amount semantics: positive for credits, negative for debits is acceptable.
#         We don't attempt to write to unknown model fields — put misc info in description/attributes.
#         """
#         attr1 = None
#         if attributes and isinstance(attributes, dict):
#             # pack some info safely into attributes_1 as JSON-like string (or simple text)
#             try:
#                 attr1 = "; ".join(f"{k}={v}" for k, v in attributes.items())
#             except Exception:
#                 attr1 = None

#         credit = Credit.objects.create(
#             company=user.company if hasattr(user, "company") else None,
#             user=user,
#             wallet=wallet,
#             action_type=action_type,
#             transaction_type=transaction_type,
#             status=status,
#             reference_id=reference_id,
#             used_credit=int(used_credit) if used_credit is not None else 0,
#             amount=Decimal(amount) if amount is not None else Decimal("0"),
#             starting_balance=Decimal(starting_balance) if starting_balance is not None else None,
#             ending_balance=Decimal(ending_balance) if ending_balance is not None else None,
#             comments=comments or "",
#             description=description or "",
#             attributes_1=attr1 or ""
#         )
#         return credit

#     @staticmethod
#     @transaction.atomic
#     def deduct_immediate(user, wallet, parts, reference_id=None, comments=None):
#         """
#         Deduct immediately from wallet.balance and create a completed Credit row.
#         parts: integer number of units to deduct (credits)
#         """
#         parts = int(parts)
#         starting_balance = Decimal(wallet.balance or 0)
#         new_balance = starting_balance - Decimal(parts)

#         # Prepaid must have enough balance
#         if wallet.plan_type == "Prepaid" and starting_balance < parts:
#             return {"ok": False, "message": "Insufficient wallet balance", "balance": starting_balance}

#         # Apply new balance (Prepaid reduces balance; Postpaid allowed to go negative)
#         wallet.balance = new_balance
#         wallet.last_updated_by = getattr(user, "username", None)
#         wallet.last_updated_date = timezone.now()
#         wallet.save(update_fields=["balance", "last_updated_by", "last_updated_date"])

#         # Ledger (store debit as negative amount)
#         credit = WalletService._create_credit_entry(
#             user=user,
#             wallet=wallet,
#             action_type="Debit",
#             transaction_type="DEDUCTION",
#             status="COMPLETED",
#             reference_id=reference_id,
#             used_credit=parts,
#             amount=Decimal(parts) * -1,
#             starting_balance=starting_balance,
#             ending_balance=new_balance,
#             comments=comments or f"Immediate deduction of {parts} units",
#             description=f"Immediate deduction for ref {reference_id}",
#             attributes={"deduction_type": "SUBMISSION"}
#         )

#         log.info("Deducted %s parts from wallet %s (user=%s). New balance: %s", parts, wallet.wallet_id, user.username, new_balance)
#         return {"ok": True, "credit": credit, "balance": new_balance}

#     @staticmethod
#     @transaction.atomic
#     def create_pending_reservation(user, wallet, parts, reference_id=None, comments=None):
#         """
#         For wallets with deduction_type == DELIVERED:
#          - If wallet.plan_type == Prepaid: ensure sufficient balance, but DO NOT change wallet.balance yet.
#            Instead create a PENDING credit reservation row (status='PENDING') which indicates reserved parts.
#          - If Postpaid, simply create the pending reservation (no balance check required).
#         Returns the pending Credit object.
#         """
#         parts = int(parts)
#         starting_balance = Decimal(wallet.balance or 0)

#         # For prepaid, check available balance (we won't actually deduct now)
#         if wallet.plan_type == "Prepaid" and starting_balance < parts:
#             return {"ok": False, "message": "Insufficient wallet balance for reservation", "balance": starting_balance}

#         # Create a PENDING credit reservation. We do not modify wallet.balance yet.
#         pending = WalletService._create_credit_entry(
#             user=user,
#             wallet=wallet,
#             action_type="Debit",
#             transaction_type="DEDUCTION",
#             status="PENDING",
#             reference_id=reference_id,
#             used_credit=parts,
#             amount=Decimal("0"),  # no actual debit yet
#             starting_balance=starting_balance,
#             ending_balance=starting_balance,
#             comments=comments or f"Pending reservation for {parts} parts",
#             description=f"Reservation for ref {reference_id}",
#             attributes={"deduction_type": "DELIVERED"}
#         )

#         log.info("Created PENDING reservation %s for %s parts (user=%s)", pending.credit_id, parts, user.username)
#         return {"ok": True, "pending": pending, "balance": starting_balance}

#     @staticmethod
#     @transaction.atomic
#     def finalize_on_delivery(user, wallet, reference_id, parts=None, dlr_status="DELIVERED", comments=None):
#         """
#         Called when DLR arrives.
#         - If there is a PENDING reservation for this reference_id, complete it:
#             - If dlr_status == DELIVERED: perform actual deduction (debit) and mark pending as COMPLETED.
#             - If dlr_status in FAILED/UNDELIVERED: mark pending as REVERSED (no balance change).
#         - If there was no PENDING reservation (i.e., deduction was immediate at submission), handle refunds for FAILED DLR.
#         """
#         # Try to find PENDING reservation
#         pending = None
#         if reference_id:
#             pending = Credit.objects.filter(reference_id=reference_id, status="PENDING", transaction_type="DEDUCTION").order_by("-created_date").first()

#         if pending:
#             # A reservation exists
#             parts_reserved = int(pending.used_credit or 0)
#             if dlr_status == "DELIVERED":
#                 # Actually deduct now
#                 starting_balance = Decimal(wallet.balance or 0)
#                 new_balance = starting_balance - Decimal(parts_reserved)

#                 if wallet.plan_type == "Prepaid" and starting_balance < parts_reserved:
#                     # Cannot deduct now (race condition) — mark pending as FAILED and report
#                     pending.status = "FAILED"
#                     pending.comments = (pending.comments or "") + " | Could not finalize: insufficient balance at delivery time"
#                     pending.last_updated_date = timezone.now()
#                     pending.save(update_fields=["status", "comments", "last_updated_date"])
#                     return {"ok": False, "message": "Insufficient balance at delivery time", "pending": pending, "balance": starting_balance}

#                 # Update wallet balance
#                 wallet.balance = new_balance
#                 wallet.last_updated_by = getattr(user, "username", None)
#                 wallet.last_updated_date = timezone.now()
#                 wallet.save(update_fields=["balance", "last_updated_by", "last_updated_date"])

#                 # Create final completed credit entry
#                 completed = WalletService._create_credit_entry(
#                     user=user,
#                     wallet=wallet,
#                     action_type="Debit",
#                     transaction_type="DEDUCTION",
#                     status="COMPLETED",
#                     reference_id=reference_id,
#                     used_credit=parts_reserved,
#                     amount=Decimal(parts_reserved) * -1,
#                     starting_balance=starting_balance,
#                     ending_balance=new_balance,
#                     comments=comments or f"Finalized deduction for delivered msg ({reference_id})",
#                     description=f"Finalized reservation {pending.credit_id}",
#                     attributes={"finalized_from": pending.credit_id}
#                 )

#                 # Mark pending as completed (preserve it for audit)
#                 pending.status = "COMPLETED"
#                 pending.last_updated_date = timezone.now()
#                 pending.comments = (pending.comments or "") + " | Finalized on delivery"
#                 pending.save(update_fields=["status", "comments", "last_updated_date"])

#                 log.info("Finalized reservation %s and debited %s parts (user=%s). New balance: %s", pending.credit_id, parts_reserved, user.username, new_balance)
#                 return {"ok": True, "completed": completed, "balance": new_balance}

#             else:
#                 # DLR failed/undelivered: mark pending as reversed (no balance change)
#                 pending.status = "REVERSED"
#                 pending.last_updated_date = timezone.now()
#                 pending.comments = (pending.comments or "") + f" | Reversed due to DLR status {dlr_status}"
#                 pending.save(update_fields=["status", "comments", "last_updated_date"])

#                 log.info("Reversed reservation %s because DLR status = %s (user=%s)", pending.credit_id, dlr_status, user.username)
#                 return {"ok": True, "reversed": pending, "balance": wallet.balance}

#         else:
#             # No pending reservation — maybe we had already deducted at submission.
#             # In that case: if dlr_status indicates failure, refund the previously deducted amount.
#             # We locate the latest completed deduction with this reference_id.
#             completed_deduction = None
#             if reference_id:
#                 completed_deduction = Credit.objects.filter(reference_id=reference_id, status="COMPLETED", transaction_type="DEDUCTION").order_by("-created_date").first()

#             if completed_deduction:
#                 if dlr_status == "DELIVERED":
#                     # Nothing to do — already deducted and delivered
#                     return {"ok": True, "message": "Already deducted and delivered", "balance": wallet.balance}
#                 else:
#                     # Refund the used_credit
#                     parts_used = int(completed_deduction.used_credit or 0)
#                     starting_balance = Decimal(wallet.balance or 0)
#                     new_balance = starting_balance + Decimal(parts_used)

#                     # Update wallet
#                     wallet.balance = new_balance
#                     wallet.last_updated_by = getattr(user, "username", None)
#                     wallet.last_updated_date = timezone.now()
#                     wallet.save(update_fields=["balance", "last_updated_by", "last_updated_date"])

#                     # Insert refund credit row
#                     refund = WalletService._create_credit_entry(
#                         user=user,
#                         wallet=wallet,
#                         action_type="Credit",
#                         transaction_type="REFUND",
#                         status="COMPLETED",
#                         reference_id=reference_id,
#                         used_credit=0,
#                         amount=Decimal(parts_used),
#                         starting_balance=starting_balance,
#                         ending_balance=new_balance,
#                         comments=comments or f"Refund for failed DLR {reference_id}",
#                         description=f"Refund for {completed_deduction.credit_id}",
#                         attributes={"refunded_from": completed_deduction.credit_id}
#                     )

#                     # Mark the original deduction as REVERSED (optional)
#                     completed_deduction.status = "REVERSED"
#                     completed_deduction.last_updated_date = timezone.now()
#                     completed_deduction.comments = (completed_deduction.comments or "") + f" | Reversed due to DLR={dlr_status}"
#                     completed_deduction.save(update_fields=["status", "comments", "last_updated_date"])

#                     log.info("Refunded %s parts for reference %s (user=%s). New balance: %s", parts_used, reference_id, user.username, new_balance)
#                     return {"ok": True, "refund": refund, "balance": new_balance}

#             # Nothing to do - neither pending nor completed found
#             return {"ok": False, "message": "No matching transaction found to finalize or refund"}




# yourapp/services/wallet_services.py
from decimal import Decimal
import logging
import json

from django.db import transaction
from django.utils import timezone
import time

from sms_app.models import Wallet, Credit, User  # adjust import path as needed

# log = logging.getLogger("wallet_service")

# class WalletService:
#     """
#     Wallet & Credit management service.
#     - Single row per batch request
#     - Backward compatible methods
#     """

#     @staticmethod
#     def get_user_wallet(user):
#         """Return the latest wallet for the user or None."""
#         if not user:
#             return None
#         return Wallet.objects.filter(user=user).order_by('-wallet_id').first()

#     @staticmethod
#     def _create_credit_entry(
#         user,
#         wallet,
#         *,
#         action_type,
#         transaction_type,
#         status,
#         reference_id,
#         used_credit,
#         amount,
#         starting_balance,
#         ending_balance,
#         comments=None,
#         description=None,
#         attributes=None,
#         bulk_details=None
#     ):
#         """
#         Create & return a Credit ledger row.
#         """
#         # Store attributes as JSON
#         attr1 = None
#         if attributes and isinstance(attributes, dict):
#             try:
#                 # Include bulk details in attributes if provided
#                 if bulk_details:
#                     attributes['bulk_details'] = bulk_details
#                 attr1 = json.dumps(attributes)
#             except Exception:
#                 attr1 = None

#         credit = Credit.objects.create(
#             company=user.company if hasattr(user, "company") else None,
#             user=user,
#             wallet=wallet,
#             action_type=action_type,
#             transaction_type=transaction_type,
#             status=status,
#             reference_id=reference_id,
#             used_credit=int(used_credit) if used_credit is not None else 0,
#             amount=Decimal(amount) if amount is not None else Decimal("0"),
#             starting_balance=Decimal(starting_balance) if starting_balance is not None else None,
#             ending_balance=Decimal(ending_balance) if ending_balance is not None else None,
#             comments=comments or "",
#             description=description or "",
#             attributes_1=attr1 or ""
#         )
#         return credit

#     @staticmethod
#     @transaction.atomic
#     def deduct_immediate(user, wallet, parts, reference_id=None, comments=None):
#         """
#         BACKWARD COMPATIBLE: For single message deduction.
#         Creates a batch request with just one message.
#         """
#         # Convert to batch with single message
#         total_parts = int(parts)
#         request_id = reference_id or f"SINGLE-{int(time.time())}-{user.user_id}"
#         reference_ids = [reference_id] if reference_id else None
        
#         return WalletService._deduct_immediate_batch(
#             user=user,
#             wallet=wallet,
#             total_parts=total_parts,
#             message_count=1,
#             request_id=request_id,
#             reference_ids=reference_ids,
#             comments=comments or f"Single message deduction"
#         )

#     @staticmethod
#     @transaction.atomic
#     def _deduct_immediate_batch(user, wallet, total_parts, message_count, request_id=None, reference_ids=None, comments=None):
#         """
#         Internal method for batch immediate deduction.
#         """
#         total_parts = int(total_parts)
#         starting_balance = Decimal(wallet.balance or 0)
#         new_balance = starting_balance - Decimal(total_parts)

#         # Prepaid must have enough balance
#         if wallet.plan_type == "Prepaid" and starting_balance < total_parts:
#             return {"ok": False, "message": "Insufficient wallet balance", "balance": starting_balance}

#         # Apply new balance
#         wallet.balance = new_balance
#         wallet.last_updated_by = getattr(user, "username", None)
#         wallet.last_updated_date = timezone.now()
#         wallet.save(update_fields=["balance", "last_updated_by", "last_updated_date"])

#         # Prepare bulk details for storage
#         bulk_details = {
#             "message_count": message_count,
#             "total_parts": total_parts,
#             "deduction_type": "SUBMISSION",
#             "deduction_time": "immediate",
#             "wallet_plan_type": wallet.plan_type
#         }
        
#         if reference_ids:
#             bulk_details["reference_ids"] = reference_ids

#         # Create single ledger entry for the entire batch
#         credit = WalletService._create_credit_entry(
#             user=user,
#             wallet=wallet,
#             action_type="Debit",
#             transaction_type="DEDUCTION",
#             status="COMPLETED",
#             reference_id=request_id,
#             used_credit=total_parts,
#             amount=Decimal(total_parts) * -1,
#             starting_balance=starting_balance,
#             ending_balance=new_balance,
#             comments=comments or f"Batch deduction of {total_parts} parts for {message_count} messages",
#             description=f"Batch deduction - Request: {request_id}",
#             attributes={"deduction_type": "SUBMISSION"},
#             bulk_details=bulk_details
#         )

#         log.info("Deducted %s total parts from wallet %s (user=%s) for %s messages. New balance: %s", 
#                  total_parts, wallet.wallet_id, user.username, message_count, new_balance)
#         return {"ok": True, "credit": credit, "balance": new_balance}

#     @staticmethod
#     @transaction.atomic
#     def create_pending_reservation(user, wallet, parts, reference_id=None, comments=None):
#         """
#         BACKWARD COMPATIBLE: For single message reservation.
#         Creates a batch reservation with just one message.
#         """
#         # Convert to batch with single message
#         total_parts = int(parts)
#         request_id = reference_id or f"RESERVE-{int(time.time())}-{user.user_id}"
#         reference_ids = [reference_id] if reference_id else None
        
#         return WalletService._create_pending_reservation_batch(
#             user=user,
#             wallet=wallet,
#             total_parts=total_parts,
#             message_count=1,
#             request_id=request_id,
#             reference_ids=reference_ids,
#             comments=comments or f"Single message reservation"
#         )

#     @staticmethod
#     @transaction.atomic
#     def _create_pending_reservation_batch(user, wallet, total_parts, message_count, request_id=None, reference_ids=None, comments=None):
#         """
#         Internal method for batch pending reservation.
#         """
#         total_parts = int(total_parts)
#         starting_balance = Decimal(wallet.balance or 0)

#         # For prepaid, check available balance
#         if wallet.plan_type == "Prepaid" and starting_balance < total_parts:
#             return {"ok": False, "message": "Insufficient wallet balance for reservation", "balance": starting_balance}

#         # Prepare bulk details
#         bulk_details = {
#             "message_count": message_count,
#             "total_parts": total_parts,
#             "deduction_type": "DELIVERED",
#             "reservation_time": "pending",
#             "wallet_plan_type": wallet.plan_type
#         }
        
#         if reference_ids:
#             bulk_details["reference_ids"] = reference_ids

#         # Create a single PENDING reservation for the entire batch
#         pending = WalletService._create_credit_entry(
#             user=user,
#             wallet=wallet,
#             action_type="Debit",
#             transaction_type="DEDUCTION",
#             status="PENDING",
#             reference_id=request_id,
#             used_credit=total_parts,
#             amount=Decimal("0"),  # no actual debit yet
#             starting_balance=starting_balance,
#             ending_balance=starting_balance,
#             comments=comments or f"Batch reservation for {total_parts} parts ({message_count} messages)",
#             description=f"Batch reservation - Request: {request_id}",
#             attributes={"deduction_type": "DELIVERED"},
#             bulk_details=bulk_details
#         )

#         log.info("Created PENDING reservation %s for %s total parts (%s messages) (user=%s)", 
#                  pending.credit_id, total_parts, message_count, user.username)
#         return {"ok": True, "pending": pending, "balance": starting_balance}

#     @staticmethod
#     @transaction.atomic
#     def create_refund_entry(user, wallet, request_id, refund_parts, reason=None, comments=None):
#         """
#         Create a refund entry for failed messages in a batch.
#         """
#         refund_parts = int(refund_parts)
#         starting_balance = Decimal(wallet.balance or 0)
#         new_balance = starting_balance + Decimal(refund_parts)

#         # Update wallet balance
#         wallet.balance = new_balance
#         wallet.last_updated_by = getattr(user, "username", None)
#         wallet.last_updated_date = timezone.now()
#         wallet.save(update_fields=["balance", "last_updated_by", "last_updated_date"])

#         # Create refund entry
#         refund = WalletService._create_credit_entry(
#             user=user,
#             wallet=wallet,
#             action_type="Credit",
#             transaction_type="REFUND",
#             status="COMPLETED",
#             reference_id=request_id,
#             used_credit=0,
#             amount=Decimal(refund_parts),
#             starting_balance=starting_balance,
#             ending_balance=new_balance,
#             comments=comments or f"Refund of {refund_parts} parts",
#             description=f"Refund for batch {request_id}",
#             attributes={"refund_reason": reason or "Failed messages"},
#             bulk_details={"refund_parts": refund_parts}
#         )

#         log.info("Created refund %s for %s parts (user=%s). New balance: %s", 
#                  refund.credit_id, refund_parts, user.username, new_balance)
#         return {"ok": True, "refund": refund, "balance": new_balance}

#     # Add these methods for batch operations
#     @staticmethod
#     def deduct_immediate_batch(user, wallet, total_parts, message_count, request_id=None, reference_ids=None, comments=None):
#         """Public batch deduction method."""
#         return WalletService._deduct_immediate_batch(user, wallet, total_parts, message_count, request_id, reference_ids, comments)

#     @staticmethod
#     def create_pending_reservation_batch(user, wallet, total_parts, message_count, request_id=None, reference_ids=None, comments=None):
#         """Public batch reservation method."""
#         return WalletService._create_pending_reservation_batch(user, wallet, total_parts, message_count, request_id, reference_ids, comments)

# yourapp/services/wallet_services.py
from decimal import Decimal
import logging
import json

from django.db import transaction
from django.utils import timezone

from sms_app.models import Wallet, Credit, User  # adjust as needed

log = logging.getLogger("wallet_service")


# --------------------------------------------------
# Batch ID Generator
# --------------------------------------------------
from django.utils import timezone

def generate_batch_id(user_id):
    """
    Format: YYMMDDHHMMSSZZ + user_id
    Example: 251215142003535123  (local time)
    """
    now = timezone.localtime(timezone.now())  # Convert UTC to local time
    timestamp = now.strftime("%y%m%d%H%M%S")
    zz = f"{now.microsecond // 10000:02d}"  # 00–99
    return f"{timestamp}{zz}{user_id}"


# --------------------------------------------------
# Wallet Service
# --------------------------------------------------
class WalletService:
    """Wallet & Credit management service with backward-compatible methods."""

    @staticmethod
    def get_user_wallet(user):
        return Wallet.objects.filter(user=user).order_by('-wallet_id').first() if user else None

    @staticmethod
    def _update_wallet_balance(wallet, user, new_balance):
        wallet.balance = new_balance
        wallet.last_updated_by = getattr(user, "username", None)
        wallet.last_updated_date = timezone.now()
        wallet.save(update_fields=["balance", "last_updated_by", "last_updated_date"])
        return new_balance

    @staticmethod
    def _create_credit(
        user, wallet, action_type, txn_type, status, reference_id,
        used_credit, amount, start_balance, end_balance,
        comments=None, description=None, attributes=None, bulk_details=None
    ):
        if attributes is None:
            attributes = {}
        if bulk_details:
            attributes["bulk_details"] = bulk_details

        attr_json = json.dumps(attributes) if attributes else ""

        return Credit.objects.create(
            company=getattr(user, "company", None),
            user=user,
            wallet=wallet,
            action_type=action_type,
            transaction_type=txn_type,
            status=status,
            reference_id=reference_id,
            used_credit=int(used_credit or 0),
            amount=Decimal(amount or 0),
            starting_balance=Decimal(start_balance) if start_balance is not None else None,
            ending_balance=Decimal(end_balance) if end_balance is not None else None,
            comments=comments or "",
            description=description or "",
            attributes_1=attr_json
        )

    # --------------------------------------------------
    # Backward-compatible single message methods
    # --------------------------------------------------
    @staticmethod
    @transaction.atomic
    def deduct_immediate(user, wallet, parts, reference_id=None, comments=None):
        total_parts = int(parts)
        request_id = reference_id or generate_batch_id(user.user_id)
        reference_ids = [reference_id] if reference_id else None

        return WalletService._deduct_batch(
            user, wallet, total_parts, 1, request_id, reference_ids, comments
        )

    @staticmethod
    @transaction.atomic
    def create_pending_reservation(user, wallet, parts, reference_id=None, comments=None):
        total_parts = int(parts)
        request_id = reference_id or generate_batch_id(user.user_id)
        reference_ids = [reference_id] if reference_id else None

        return WalletService._reserve_batch(
            user, wallet, total_parts, 1, request_id, reference_ids, comments
        )

    # --------------------------------------------------
    # Internal batch methods
    # --------------------------------------------------
    @staticmethod
    @transaction.atomic
    def _deduct_batch(user, wallet, total_parts, message_count,
                      request_id=None, reference_ids=None, comments=None):

        total_parts = int(total_parts)
        start_balance = Decimal(wallet.balance or 0)

        if wallet.plan_type == "Prepaid" and start_balance < total_parts:
            return {"ok": False, "message": "Insufficient balance", "balance": start_balance}

        new_balance = start_balance - Decimal(total_parts)
        WalletService._update_wallet_balance(wallet, user, new_balance)

        request_id = request_id or generate_batch_id(user.user_id)

        bulk_details = {
            "message_count": message_count,
            "total_parts": total_parts,
            "deduction_type": "SUBMISSION",
            "wallet_plan_type": wallet.plan_type
        }
        if reference_ids:
            bulk_details["reference_ids"] = reference_ids

        credit = WalletService._create_credit(
            user=user,
            wallet=wallet,
            action_type="Debit",
            txn_type="DEDUCTION",
            status="COMPLETED",
            reference_id=request_id,
            used_credit=total_parts,
            amount=-total_parts,
            start_balance=start_balance,
            end_balance=new_balance,
            comments=comments or f"Deduction of {total_parts} parts",
            description=f"Batch deduction - {request_id}",
            attributes={"deduction_type": "SUBMISSION"},
            bulk_details=bulk_details
        )

        log.info(
            "Deducted %s parts | user=%s | batch_id=%s | balance=%s",
            total_parts, user.username, request_id, new_balance
        )

        # ✅ FIX: batch_id added
        return {
            "ok": True,
            "batch_id": request_id,
            "credit": credit,
            "balance": new_balance
        }

    @staticmethod
    @transaction.atomic
    def _reserve_batch(user, wallet, total_parts, message_count,
                       request_id=None, reference_ids=None, comments=None):

        total_parts = int(total_parts)
        start_balance = Decimal(wallet.balance or 0)

        if wallet.plan_type == "Prepaid" and start_balance < total_parts:
            return {"ok": False, "message": "Insufficient balance for reservation", "balance": start_balance}

        request_id = request_id or generate_batch_id(user.user_id)

        bulk_details = {
            "message_count": message_count,
            "total_parts": total_parts,
            "deduction_type": "DELIVERED",
            "wallet_plan_type": wallet.plan_type
        }
        if reference_ids:
            bulk_details["reference_ids"] = reference_ids

        pending = WalletService._create_credit(
            user=user,
            wallet=wallet,
            action_type="Debit",
            txn_type="DEDUCTION",
            status="PENDING",
            reference_id=request_id,
            used_credit=total_parts,
            amount=0,
            start_balance=start_balance,
            end_balance=start_balance,
            comments=comments or f"Reservation for {total_parts} parts",
            description=f"Batch reservation - {request_id}",
            attributes={"deduction_type": "DELIVERED"},
            bulk_details=bulk_details
        )

        log.info(
            "Created PENDING reservation | user=%s | batch_id=%s | parts=%s",
            user.username, request_id, total_parts
        )

        # ✅ FIX: batch_id added
        return {
            "ok": True,
            "batch_id": request_id,
            "pending": pending,
            "balance": start_balance
        }

    # --------------------------------------------------
    # Refund
    # --------------------------------------------------
    @staticmethod
    @transaction.atomic
    def create_refund_entry(user, wallet, request_id, refund_parts, reason=None, comments=None):
        refund_parts = int(refund_parts)
        start_balance = Decimal(wallet.balance or 0)
        new_balance = start_balance + Decimal(refund_parts)

        WalletService._update_wallet_balance(wallet, user, new_balance)

        refund = WalletService._create_credit(
            user=user,
            wallet=wallet,
            action_type="Credit",
            txn_type="REFUND",
            status="COMPLETED",
            reference_id=request_id,
            used_credit=0,
            amount=refund_parts,
            start_balance=start_balance,
            end_balance=new_balance,
            comments=comments or f"Refund of {refund_parts} parts",
            description=f"Refund for batch {request_id}",
            attributes={"refund_reason": reason or "Failed messages"},
            bulk_details={"refund_parts": refund_parts}
        )

        log.info(
            "Refunded %s parts | user=%s | batch_id=%s | balance=%s",
            refund_parts, user.username, request_id, new_balance
        )
        return {"ok": True, "refund": refund, "balance": new_balance}
