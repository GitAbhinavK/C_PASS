# sms_app/utils/credit_manager.py

from django.db import transaction
from django.core.exceptions import ValidationError
from sms_app.models import Credit
import traceback


def manage_user_credits(user, message_count, created_by="system"):
    """
    Deduct message credits when SMS messages are sent.
    Rolls back automatically if insufficient credits.
    """

    print("\n==============================")
    print("💳 CREDIT MANAGER STARTED")
    print("==============================")
    print(f"User: {user.username} (ID={user.user_id})")
    print(f"Message Count to Deduct: {message_count}")
    print(f"Action Triggered By: {created_by}")

    try:
        with transaction.atomic():
            # 🧩 Fetch or create credit record
            credit_obj, created = Credit.objects.get_or_create(
                user=user,
                defaults={
                    "username": user.username,
                    "credits": 0,
                    "used_credits": 0,
                    "available_credits": 0,
                    "create_by": created_by,
                },
            )

            if created:
                print("🆕 New Credit record created for user.")
            else:
                print(f"✅ Existing Credit record found (Available Credits: {credit_obj.available_credits})")

            # 🧮 Debug all credit details before deduction
            print(f"--- CREDIT DETAILS BEFORE DEDUCTION ---")
            print(f"Total Credits: {credit_obj.credits}")
            print(f"Used Credits: {credit_obj.used_credits}")
            print(f"Available Credits: {credit_obj.available_credits}")
            print("----------------------------------------")

            # 🧠 Check if user has enough credits
            if credit_obj.available_credits < message_count:
                print("❌ Insufficient credits!")
                raise ValidationError(
                    f"Not enough credits! You have {credit_obj.available_credits}, need {message_count}."
                )

            # 🧾 Deduct credits
            credit_obj.used_credits += message_count
            credit_obj.available_credits -= message_count
            credit_obj.action_type = "Debit"
            credit_obj.last_updated_by = created_by
            credit_obj.save()

            print("✅ Credit deduction successful!")
            print(f"Remaining Credits: {credit_obj.available_credits}")

            print("==============================")
            print("💳 CREDIT MANAGER COMPLETED")
            print("==============================\n")

            return {
                "status": "success",
                "message": f"{message_count} credits deducted successfully.",
                "available_credits": credit_obj.available_credits
            }

    except ValidationError as e:
        print("⚠️ ValidationError in Credit Manager:")
        print(traceback.format_exc())
        return {
            "status": "error",
            "message": str(e)
        }

    except Exception as e:
        print("💥 Unexpected Exception in Credit Manager:")
        print(traceback.format_exc())
        return {
            "status": "error",
            "message": f"Error managing credits: {e}"
        }
