from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Role, Company
from django.views import View
from .models import Credit, Sender, MessageTemplate,ComposeMessageLine
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator
from django.contrib.auth.views import LoginView
from django.contrib.auth.forms import AuthenticationForm
import logging
import threading
from django.db import transaction
from django.db import close_old_connections
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from decimal import Decimal
from django.db.models import Q, Sum, Case, When, IntegerField, DecimalField, Count
import datetime
from django.db import transaction
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.http import JsonResponse
import os
from .models import Company
from datetime import date
from django.core.validators import validate_email
from django.db import IntegrityError
from django.conf import settings


# Redirect root "/" to login if not authenticated, else to dashboard
def home_redirect(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('login')

logger = logging.getLogger(__name__)
@sensitive_post_parameters('password')
@never_cache
@csrf_protect
def custom_login_view(request):
    """
    Enterprise-level login view with enhanced security and error handling
    """
    # Redirect authenticated users
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    # Initialize form
    form = AuthenticationForm(data=request.POST or None)
    
    if request.method == 'POST':
        if form.is_valid():
            try:
                username = form.cleaned_data.get('username')
                password = form.cleaned_data.get('password')
                user = authenticate(request, username=username, password=password)
                
                if user is not None:
                    login(request, user)
                    
                    # Log successful login
                    logger.info(f"User {username} logged in successfully from IP: {request.META.get('REMOTE_ADDR')}")
                    
                    # Check for next parameter
                    next_url = request.POST.get('next', '')
                    if next_url:
                        return redirect(next_url)
                    return redirect('dashboard')
                else:
                    # Invalid credentials
                    messages.error(request, 'Invalid username or password.')
                    logger.warning(f"Failed login attempt for username: {username} from IP: {request.META.get('REMOTE_ADDR')}")
            
            except Exception as e:
                # Log unexpected errors
                logger.error(f"Login error: {str(e)}")
                messages.error(request, 'An error occurred during login. Please try again.')
        else:
            # Form validation errors
            for error in form.errors.values():
                messages.error(request, error)
    
    # Get next parameter for redirect after login
    next_url = request.GET.get('next', '')
    
    return render(request, 'login.html', {
        'form': form,
        'next': next_url,
        'messages': messages.get_messages(request)
    })

# Protected dashboard page
@login_required
def dashboard(request):
    from .models import User as CustomUser, ComposeMessage, ComposeMessageLine

    user = request.user
    user_role = getattr(getattr(user, 'role', None), 'role_name', None)

    if user.is_superuser:
        # Django superuser sees everything across all companies
        total_users = CustomUser.objects.count()
        company_user_ids = list(CustomUser.objects.values_list('user_id', flat=True))
    elif user.company:
        # All other roles scoped to their own company
        company_users = CustomUser.objects.filter(company=user.company)
        total_users = company_users.count() if user_role != 'Client' else 0
        company_user_ids = list(company_users.values_list('user_id', flat=True))
    else:
        total_users = 0
        company_user_ids = [user.user_id]

    # SMS sent — count lines belonging to compose messages owned by scoped users
    sms_sent = ComposeMessageLine.objects.filter(
        compose_message__user_id__in=company_user_ids
    ).count()

    context = {
        'user': user,
        'total_users': total_users,
        'sms_sent': sms_sent,
        'pending_campaigns': 0,
        'active_plans': 0,
        'show_users_card': user_role != 'Client',
    }
    return render(request, 'dashboard.html', context)

@login_required
def setting(request):
    return render(request, 'settings_page.html')

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Count
from .models import ComposeMessageLine, ComposeMessage
from django.utils import timezone
from datetime import datetime, timedelta

@login_required
def sms_summary_view(request):
    # Get filter parameters from request
    status_filter = request.GET.get('status', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    message_id_filter = request.GET.get('message_id', '')
    mobile_filter = request.GET.get('mobile', '')
    user_id_filter = request.GET.get('user_id', '')
    
    # Base queryset
    lines = ComposeMessageLine.objects.all().select_related('compose_message')
    
    # Apply filters
    if status_filter:
        lines = lines.filter(status=status_filter)
    
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            lines = lines.filter(submit_time__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            lines = lines.filter(submit_time__lt=date_to_obj)
        except ValueError:
            pass
    
    if message_id_filter:
        lines = lines.filter(message_id__icontains=message_id_filter)
    
    if mobile_filter:
        lines = lines.filter(mobile_number__icontains=mobile_filter)
    
    if user_id_filter:
        lines = lines.filter(compose_message__user_id=user_id_filter)
    
    # Status categories for counting - INCLUDING UNDELIVERED
    delivered_status = ['DELIVRD', 'DELIVERED', 'DELIVERED TO DESTINATION', 'SUCCESS']
    submitted_status = ['SUBMITTED', 'SENT', 'ACCEPTED', 'DISPATCHED']
    undelivered_status = ['UNDELIV', 'UNDELIVERED', 'FAILED', 'REJECTED', 'EXPIRED', 'UNDELIVERABLE']
    pending_status = ['PENDING', 'ENROUTE', 'INPROGRESS', 'WAITING', 'PROCESSING']
    
    # Totals based on filtered queryset
    total_delivered = lines.filter(status__in=delivered_status).count()
    total_submitted = lines.filter(status__in=submitted_status).count()
    total_failed = lines.filter(status__in=undelivered_status).count()
    total_pending = lines.filter(status__in=pending_status).count()
    total_all = lines.count()
    
    # Get unique status values for dropdown
    status_choices = ComposeMessageLine.objects.values_list('status', flat=True).distinct().order_by('status')
    
    context = {
        'lines': lines.order_by('-submit_time')[:100],  # Limit to last 100 records for performance
        'total_delivered': total_delivered,
        'total_submitted': total_submitted,
        'total_failed': total_failed,
        'total_pending': total_pending,
        'total_all': total_all,
        'status_choices': status_choices,
        'delivered_status': delivered_status,
        'submitted_status': submitted_status,
        'undelivered_status': undelivered_status,
        'pending_status': pending_status,
        'filters': {
            'status': status_filter,
            'date_from': date_from,
            'date_to': date_to,
            'message_id': message_id_filter,
            'mobile': mobile_filter,
            'user_id': user_id_filter,
        }
    }
    return render(request, 'compose_summary.html', context)

# @login_required
# def dlr_history_view(request):
#     lines = ComposeMessageLine.objects.all().order_by('-dlr_time')
#     return render(request, 'dlr_history.html', {
#         'lines': lines,
#     })

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import ComposeMessageLine
from datetime import datetime, timedelta

@login_required
def dlr_history_view(request):
    # Get filter parameters from request
    status_filter = request.GET.get('status', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    message_id_filter = request.GET.get('message_id', '')
    mobile_filter = request.GET.get('mobile', '')
    tmid_filter = request.GET.get('tmid', '')
    smsc_filter = request.GET.get('smsc', '')
    account_filter = request.GET.get('account', '')
    
    # Base queryset
    lines = ComposeMessageLine.objects.all()
    
    # Apply filters
    if status_filter:
        if status_filter == 'DELIVERED':
            lines = lines.filter(status__in=['DELIVRD', 'DELIVERED'])
        elif status_filter == 'FAILED':
            lines = lines.filter(status__in=['UNDELIV', 'UNDELIVERED', 'FAILED', 'REJECTED', 'EXPIRED'])
        elif status_filter == 'PENDING':
            lines = lines.filter(status__in=['PENDING', 'ENROUTE', 'SUBMITTED'])
        elif status_filter == 'SUBMITTED':
            lines = lines.filter(status__in=['SUBMITTED', 'SENT', 'ACCEPTED'])
        else:
            lines = lines.filter(status=status_filter)
    
    if date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            lines = lines.filter(submit_time__gte=date_from_obj)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            lines = lines.filter(submit_time__lt=date_to_obj)
        except ValueError:
            pass
    
    if message_id_filter:
        lines = lines.filter(message_id__icontains=message_id_filter)
    
    if mobile_filter:
        lines = lines.filter(
            Q(mobile_number__icontains=mobile_filter) | 
            Q(receiver__icontains=mobile_filter)
        )
    
    if tmid_filter:
        lines = lines.filter(tmid__icontains=tmid_filter)
    
    if smsc_filter:
        lines = lines.filter(smsc__icontains=smsc_filter)
    
    if account_filter:
        lines = lines.filter(account__icontains=account_filter)
    
    # Get unique values for filter dropdowns
    status_choices = ComposeMessageLine.objects.exclude(status__isnull=True).exclude(status='')\
                       .values_list('status', flat=True).distinct().order_by('status')
    smsc_choices = ComposeMessageLine.objects.exclude(smsc__isnull=True).exclude(smsc='')\
                    .values_list('smsc', flat=True).distinct().order_by('smsc')[:20]
    account_choices = ComposeMessageLine.objects.exclude(account__isnull=True).exclude(account='')\
                       .values_list('account', flat=True).distinct().order_by('account')[:20]
    
    # Statistics
    total_count = lines.count()
    delivered_count = lines.filter(status__in=['DELIVRD', 'DELIVERED']).count()
    failed_count = lines.filter(status__in=['UNDELIV', 'UNDELIVERED', 'FAILED', 'REJECTED', 'EXPIRED']).count()
    pending_count = lines.filter(status__in=['PENDING', 'ENROUTE', 'SUBMITTED']).count()
    
    context = {
        'lines': lines.order_by('-submit_time')[:500],  # Limit for performance
        'status_choices': status_choices,
        'smsc_choices': smsc_choices,
        'account_choices': account_choices,
        'total_count': total_count,
        'delivered_count': delivered_count,
        'failed_count': failed_count,
        'pending_count': pending_count,
        'filters': {
            'status': status_filter,
            'date_from': date_from,
            'date_to': date_to,
            'message_id': message_id_filter,
            'mobile': mobile_filter,
            'tmid': tmid_filter,
            'smsc': smsc_filter,
            'account': account_filter,
        }
    }
    return render(request, 'dlr_history.html', context)

# =========================
# AJAX Template
# =========================
def get_template_by_sender(request):
    sender_id = request.GET.get('sender_id')
    if not sender_id:
        return JsonResponse([], safe=False)
    templates = MessageTemplate.objects.filter(sender__sender_id=sender_id).values(
        'id', 'template_identifier', 'message_template', 'dlt_template_id', 'dlt_template_type'
    )
    return JsonResponse(list(templates), safe=False)

# credit controller views.py
def is_superuser_or_admin(user):
    """Check if user is superuser, super admin, or support admin"""
    if user.is_superuser:
        return True
    if hasattr(user, 'role') and user.role:
        return user.role.role_name in ['Super Admin', 'Support Admin']
    return False

def is_superuser_or_admin(user):
    """Check if user is superuser, super admin, or support admin"""
    if user.is_superuser:
        return True
    if hasattr(user, 'role') and user.role:
        return user.role.role_name in ['Super Admin', 'Support Admin']
    return False

@login_required
def credit_list(request):
    User = get_user_model()
    
    # Get all filter parameters
    search_query = request.GET.get('search', '')
    user_filter = request.GET.get('user', '')
    action_filter = request.GET.get('action_type', '')
    status_filter = request.GET.get('status', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    transaction_type_filter = request.GET.get('transaction_type', '')
    min_amount = request.GET.get('min_amount', '')
    max_amount = request.GET.get('max_amount', '')
    
    # Base queryset
    if is_superuser_or_admin(request.user):
        # Admin can see all credits
        credits = Credit.objects.select_related('user', 'company', 'wallet').all()
    else:
        # Regular users can only see their own credits
        credits = Credit.objects.select_related('user', 'company', 'wallet').filter(
            user=request.user
        )
    
    # Apply filters
    if search_query:
        credits = credits.filter(
            Q(credit_id__icontains=search_query) |
            Q(user__username__icontains=search_query) |
            Q(comments__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(reference_id__icontains=search_query)
        )
    
    if user_filter:
        credits = credits.filter(user_id=user_filter)
    
    if action_filter:
        credits = credits.filter(action_type=action_filter)
    
    if status_filter:
        credits = credits.filter(status=status_filter)
    
    if transaction_type_filter:
        credits = credits.filter(transaction_type=transaction_type_filter)
    
    if date_from:
        credits = credits.filter(created_date__date__gte=date_from)
    
    if date_to:
        credits = credits.filter(created_date__date__lte=date_to)
    
    if min_amount:
        try:
            min_val = Decimal(min_amount)
            credits = credits.filter(amount__gte=min_val)
        except:
            pass
    
    if max_amount:
        try:
            max_val = Decimal(max_amount)
            credits = credits.filter(amount__lte=max_val)
        except:
            pass
    
    # Order by
    credits = credits.order_by('-created_date')
    
    # Get statistics
    stats = credits.aggregate(
        total_credit=Sum(
            Case(
                When(action_type='Credit', then='amount'),
                default=0,
                output_field=DecimalField(max_digits=15, decimal_places=2)
            )
        ),
        total_debit=Sum(
            Case(
                When(action_type='Debit', then='amount'),
                default=0,
                output_field=DecimalField(max_digits=15, decimal_places=2)
            )
        ),
        total_transactions=Count('credit_id'),
        total_balance=Sum('amount')
    )
    
    # Handle None values in stats
    if stats['total_credit'] is None:
        stats['total_credit'] = Decimal('0.00')
    if stats['total_debit'] is None:
        stats['total_debit'] = Decimal('0.00')
    if stats['total_balance'] is None:
        stats['total_balance'] = Decimal('0.00')
    
    # Pagination
    paginator = Paginator(credits, 50)  # 50 items per page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Get users for filter dropdown (only for admin)
    if is_superuser_or_admin(request.user):
        users = User.objects.filter(is_active=True).values('user_id', 'username')
    else:
        users = User.objects.filter(user_id=request.user.user_id).values('user_id', 'username')
    
    context = {
        'credits': page_obj,
        'users': users,
        'stats': stats,
        'search_query': search_query,
        'user_filter': user_filter,
        'action_filter': action_filter,
        'status_filter': status_filter,
        'date_from': date_from,
        'date_to': date_to,
        'transaction_type_filter': transaction_type_filter,
        'min_amount': min_amount,
        'max_amount': max_amount,
        'is_admin': is_superuser_or_admin(request.user),
        'user_company': request.user.company if hasattr(request.user, 'company') else None,
    }
    
    return render(request, 'credits.html', context)
@login_required
@transaction.atomic
def add_credit(request):
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        action_type = request.POST.get('action_type')
        credits_value = int(request.POST.get('credits') or 0)
        comments = request.POST.get('comments', '')

        # ✅ Correct user lookup
        User = get_user_model()
        user = User.objects.get(user_id=user_id)

        # ✅ Fetch last record
        last_record = Credit.objects.filter(user=user).order_by('-credit_id').first()
        available_before = last_record.available_credits if last_record else 0
        total_before = last_record.total_credits if last_record else 0

        # ✅ Apply arithmetic
        if action_type == "Credit":
            total_after = total_before + credits_value
            available_after = available_before + credits_value
        elif action_type == "Debit":
            total_after = total_before
            available_after = max(available_before - credits_value, 0)
        else:
            messages.error(request, "Invalid action type.")
            return redirect('credit_list')

        # ✅ Create new credit record
        Credit.objects.create(
            user=user,
            username=user.username,
            action_type=action_type,
            credits=credits_value,
            total_credits=total_after,
            used_credits=total_after - available_after,
            available_credits=available_after,
            comments=comments,
            create_by=request.user.username,
        )

        messages.success(
            request,
            f"✅ {action_type} of {credits_value} applied. Total: {total_after}, Available: {available_after}"
        )
        return redirect('credit_list')

    return redirect('credit_list')

@login_required
@transaction.atomic
def edit_credit(request, credit_id):
    if request.method != 'POST':
        messages.error(request, "❌ Invalid request.")
        return redirect('credit_list')

    print("🟢 Edit request received for Credit ID:", credit_id)
    print("🔹 POST Data:", request.POST.dict())

    credit = get_object_or_404(Credit, credit_id=credit_id)
    user = credit.user

    new_action = request.POST.get('action_type')
    new_credits_raw = request.POST.get('credits')
    new_credits = int(new_credits_raw or 0)
    comments = request.POST.get('comments', '')

    print(f"🔸 Existing Record: action={credit.action_type}, credits={credit.credits}")
    print(f"🔸 New Values: action={new_action}, credits={new_credits}, comments={comments}")

    # ✅ Fetch latest values
    last_record = Credit.objects.filter(user=user).order_by('-credit_id').first()
    available_before = last_record.available_credits if last_record else 0
    total_before = last_record.total_credits if last_record else 0
    print(f"📊 Before Undo: total={total_before}, available={available_before}")

    # ✅ Undo old entry effect
    if credit.action_type == "Credit":
        available_before -= credit.credits
        total_before -= credit.credits
    elif credit.action_type == "Debit":
        available_before += credit.credits

    print(f"📊 After Undo: total={total_before}, available={available_before}")

    # ✅ Apply updated operation
    if new_action == "Credit":
        total_after = total_before + new_credits
        available_after = available_before + new_credits
    elif new_action == "Debit":
        total_after = total_before
        available_after = max(available_before - new_credits, 0)
    else:
        messages.error(request, "❌ Invalid action type.")
        return redirect('credit_list')

    print(f"✅ Final Calculated Totals: total={total_after}, available={available_after}")

    # ✅ Update record
    credit.action_type = new_action
    credit.credits = new_credits
    credit.comments = comments
    credit.total_credits = total_after
    credit.available_credits = available_after
    credit.last_updated_by = request.user.username
    credit.save()

    print(f"💾 Credit record {credit.credit_id} updated successfully for user {user.username}")

    messages.success(
        request,
        f"✅ Credit updated successfully. Total: {total_after}, Available: {available_after}"
    )
    return redirect('credit_list')


def get_username(request, user_id):
    try:
        User = get_user_model()
        user = User.objects.get(user_id=user_id)
        return JsonResponse({'username': user.username})
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

# end credit controller views.py
# company controller views.py
# ===============================================================================


@login_required
def company_list(request):
    companies = Company.objects.all().order_by('-created_date')
    user_company = getattr(request.user, 'company', None)
    today = timezone.now().date()
    
    # Check for company-specific messages
    company_messages = []
    if messages.get_messages(request):
        for message in messages.get_messages(request):
            if 'company' in message.extra_tags:
                company_messages.append({
                    'level': message.level,
                    'message': message.message,
                    'tags': message.tags
                })

    return render(request, "company_list2.html", {
        "companies": companies,
        "user_company": user_company,
        "today": today,
        "company_messages": company_messages,
    })


@login_required
@transaction.atomic
def add_company(request):
    if request.method == "POST":
        name = request.POST.get("name")
        email = request.POST.get("email")
        address = request.POST.get("address")
        mobile = request.POST.get("mobile")
        country_code = request.POST.get("country_code", "+91")
        domain_name = request.POST.get("domain_name")
        theme_color = request.POST.get("theme_color", "#e65100")
        allowed_ip = request.POST.get("allowed_ip")
        active_status = True if request.POST.get("active_status") in ["on", "true", "1"] else False
        logo_file = request.FILES.get("logo")
        favicon_file = request.FILES.get("favicon")

        # Validate required fields
        if not name or not email or not address:
            messages.error(request, "❌ Company name, email, and address are required fields.", extra_tags='company')
            return redirect("company_list")
        
        if not logo_file or not favicon_file:
            messages.error(request, "❌ Logo and favicon are required.", extra_tags='company')
            return redirect("company_list")

        # Combine country code with mobile
        full_mobile = f"{country_code} {mobile}" if mobile else ""

        # auto system start_date
        start_date = timezone.now().date()

        company = Company.objects.create(
            name=name,
            email=email,
            address=address,
            mobile=full_mobile,
            domain_name=domain_name,
            attributes_1=theme_color,
            allowed_ip=allowed_ip,
            active_status=active_status,
            create_by=request.user.username,
            start_date=start_date,
        )

        if logo_file:
            ext = os.path.splitext(logo_file.name)[1] or ".jpg"
            logo_filename = f"logos/logo_{company.company_id}{ext}"
            logo_path = default_storage.save(logo_filename, ContentFile(logo_file.read()))
            company.logo = logo_path

        if favicon_file:
            ext = os.path.splitext(favicon_file.name)[1] or ".ico"
            favicon_filename = f"favicons/favicon_{company.company_id}{ext}"
            favicon_path = default_storage.save(favicon_filename, ContentFile(favicon_file.read()))
            company.favicon = favicon_path

        company.save()
        messages.success(request, f"✅ Company '{company.name}' added successfully!", extra_tags='company')
        return redirect("company_list")

    return redirect("company_list")


@login_required
@transaction.atomic
def edit_company(request, company_id):
    company = get_object_or_404(Company, company_id=company_id)

    if request.method == "POST":
        try:
            company.name = request.POST.get("name")
            company.email = request.POST.get("email")
            company.address = request.POST.get("address")
            
            # Handle mobile number
            mobile_full = request.POST.get("mobile", "").strip()
            if mobile_full:
                company.mobile = mobile_full
            
            company.domain_name = request.POST.get("domain_name")
            company.attributes_1 = request.POST.get("theme_color", company.attributes_1)
            company.allowed_ip = request.POST.get("allowed_ip")
            company.active_status = True if request.POST.get("active_status") in ["on", "true", "1"] else False

            # editable dates
            start_date = request.POST.get("start_date")
            if start_date:
                company.start_date = start_date
            
            end_date = request.POST.get("end_date")
            if end_date:
                company.end_date = end_date
            else:
                company.end_date = None

            company.last_updated_by = request.user.username

            logo_file = request.FILES.get("logo")
            favicon_file = request.FILES.get("favicon")

            if logo_file:
                # Delete old logo if exists
                if company.logo:
                    try:
                        default_storage.delete(company.logo.path)
                    except:
                        pass
                
                ext = os.path.splitext(logo_file.name)[1] or ".jpg"
                logo_filename = f"logos/logo_{company.company_id}{ext}"
                logo_path = default_storage.save(logo_filename, ContentFile(logo_file.read()))
                company.logo = logo_path

            if favicon_file:
                # Delete old favicon if exists
                if company.favicon:
                    try:
                        default_storage.delete(company.favicon.path)
                    except:
                        pass
                
                ext = os.path.splitext(favicon_file.name)[1] or ".ico"
                favicon_filename = f"favicons/favicon_{company.company_id}{ext}"
                favicon_path = default_storage.save(favicon_filename, ContentFile(favicon_file.read()))
                company.favicon = favicon_path

            company.save()
            
            # Check if it's an AJAX request
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': f"✅ Company '{company.name}' updated successfully!"
                })
            else:
                messages.success(request, f"✅ Company '{company.name}' updated successfully!", extra_tags='company')
                return redirect("company_list")
                
        except Exception as e:
            # Check if it's an AJAX request
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'message': f"❌ Error updating company: {str(e)}"
                }, status=400)
            else:
                messages.error(request, f"❌ Error updating company: {str(e)}", extra_tags='company')
                return redirect("company_list")
    
    # If it's a GET request, return JSON for AJAX or redirect
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'error': 'GET method not allowed for edit'
        }, status=405)
    else:
        return redirect("company_list")


@login_required
def get_company(request, company_id):
    """Get company data for AJAX editing"""
    try:
        company = Company.objects.get(company_id=company_id)
        
        # Extract mobile number without country code for display
        mobile_display = company.mobile or ""
        if " " in mobile_display:
            # Split country code and number
            parts = mobile_display.split()
            if len(parts) > 1:
                mobile_display = parts[1]  # Just the number part
        
        data = {
            'company_id': company.company_id,
            'name': company.name,
            'email': company.email,
            'address': company.address,
            'mobile': mobile_display,
            'domain_name': company.domain_name or '',
            'theme_color': company.attributes_1 or '#e65100',
            'allowed_ip': company.allowed_ip or '',
            'active_status': company.active_status,
            'start_date': company.start_date.strftime('%Y-%m-%d') if company.start_date else '',
            'end_date': company.end_date.strftime('%Y-%m-%d') if company.end_date else '',
            'logo_url': company.logo.url if company.logo and hasattr(company.logo, 'url') else '',
            'favicon_url': company.favicon.url if company.favicon and hasattr(company.favicon, 'url') else '',
            'has_logo': bool(company.logo),
            'has_favicon': bool(company.favicon),
        }
        
        return JsonResponse(data)
    except Company.DoesNotExist:
        return JsonResponse({'error': 'Company not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

# ===================================================================================

@login_required
def role_list(request):
    user = request.user

    # 1️⃣ Superuser sees all roles
    if user.is_superuser:
        roles = Role.objects.select_related('company').all().order_by('role_id')

    # 2️⃣ Normal user sees only roles of their company
    else:
        # Assuming user has a 'company' field in profile or user model
        if hasattr(user, 'company') and user.company:
            roles = Role.objects.select_related('company') \
                                .filter(company=user.company) \
                                .order_by('role_id')
        else:
            # If user has no company, show empty
            roles = Role.objects.none()

    # Active companies (for dropdown or filters)
    companies = Company.objects.filter(active_status=True)
    
    # Static role choices
    role_choices = Role.ROLE_CHOICES

    return render(request, 'role_list.html', {
        'roles': roles,
        'companies': companies,
        'role_choices': role_choices
    })


def add_role(request):
    if request.method == 'POST':
        company_id = request.POST['company']
        role_name = request.POST['role_name']

        # Check for duplicate role for the same company
        if Role.objects.filter(company_id=company_id, role_name=role_name).exists():
            messages.error(request, "This role already exists for the selected company.")
            return redirect('role_list')

        start_date = date.today()
        end_date = request.POST.get('end_date')
        flag = 'flag' in request.POST

        try:
            company = Company.objects.get(pk=company_id)
            Role.objects.create(
                company=company,
                role_name=role_name,
                start_date=start_date or None,
                end_date=end_date or None,
                flag=flag
            )
            messages.success(request, "Role added successfully!")
        except Exception as e:
            messages.error(request, f"Error adding role: {str(e)}")

        return redirect('role_list')

def edit_role(request):
    if request.method == 'POST':
        role = get_object_or_404(Role, pk=request.POST['role_id'])
        role_name = request.POST['role_name']

        # Prevent duplicate role for same company
        if Role.objects.filter(company_id=role.company_id, role_name=role_name).exclude(pk=role.role_id).exists():
            messages.error(request, "This role already exists for the company.")
            return redirect('role_list')

        role.role_name = role_name
        # role.start_date = request.POST.get('start_date') or None
        # role.end_date = request.POST.get('end_date') or None
        role.flag = 'flag' in request.POST

        try:
            role.save()
            messages.success(request, "Role updated successfully!")
        except Exception as e:
            messages.error(request, f"Error updating role: {str(e)}")

        return redirect('role_list')


def get_accessible_senders_queryset(user, active_only=False):
    senders = Sender.objects.all()

    if active_only:
        senders = senders.filter(active_flag=True)

    if getattr(user, "is_superuser", False):
        return senders.order_by('-last_updated_date').distinct()

    user_company = getattr(user, "company", None)
    sender_filter = Q(user=user) | Q(user__isnull=True)
    if user_company:
        sender_filter |= Q(user__company=user_company)

    filtered = senders.filter(sender_filter).order_by('-last_updated_date').distinct()

    user_role = getattr(getattr(user, "role", None), "role_name", "")
    if filtered.exists() or user_role != "Client":
        return filtered

    if user_company:
        fallback = senders.filter(Q(user__company=user_company) | Q(user__isnull=True))
    else:
        fallback = senders

    return fallback.order_by('-last_updated_date').distinct()


def get_accessible_templates_queryset(user):
    templates = MessageTemplate.objects.select_related('user', 'sender')

    if getattr(user, "is_superuser", False):
        return templates.order_by('-last_updated_date').distinct()

    user_company = getattr(user, "company", None)
    template_filter = (
        Q(user=user) |
        Q(user__isnull=True) |
        Q(sender__user=user) |
        Q(sender__user__isnull=True)
    )
    if user_company:
        template_filter |= Q(user__company=user_company) | Q(sender__user__company=user_company)

    filtered = templates.filter(template_filter).order_by('-last_updated_date').distinct()

    user_role = getattr(getattr(user, "role", None), "role_name", "")
    if filtered.exists() or user_role != "Client":
        return filtered

    if user_company:
        fallback = templates.filter(
            Q(user__company=user_company) |
            Q(sender__user__company=user_company) |
            Q(user__isnull=True) |
            Q(sender__user__isnull=True)
        )
    else:
        fallback = templates

    return fallback.order_by('-last_updated_date').distinct()


@login_required
def sender_list(request):
    senders = get_accessible_senders_queryset(request.user)
    
    # Check for sender-specific messages and pass them to template
    sender_messages = []
    if messages.get_messages(request):
        for message in messages.get_messages(request):
            if 'sender' in message.extra_tags:
                sender_messages.append({
                    'level': message.level,
                    'message': message.message,
                    'tags': message.tags
                })
    
    return render(request, 'sender_templates.html', {
        'senders': senders,
        'sender_messages': sender_messages
    })

def get_sender(request, sender_id):
    """Get sender data for editing"""
    try:
        print(f"Fetching sender with ID: {sender_id}")  # Debug
        sender = Sender.objects.get(pk=sender_id)
        
        # Get user and account type
        user = sender.user
        user_name = user.username if user else "System"
        user_id = user.user_id if user else None
        
        # Get account type
        if user:
            account_type = getattr(user, 'account_type', '') or getattr(user, 'sms_account_type', '') or ''
        else:
            # If no user is assigned, get current user's account type
            current_user = request.user
            account_type = getattr(current_user, 'account_type', '') or getattr(current_user, 'sms_account_type', '') or ''
        
        data = {
            'sender_id': sender.sender_id,
            'sender_name': sender.sender_name,
            'peid': sender.peid or '',
            'active_flag': sender.active_flag,
            'user_id': user_id or request.user.user_id,
            'user_name': user_name,
            'account_type': account_type.lower(),
        }
        print(f"Returning data: {data}")  # Debug
        return JsonResponse(data)
    except Sender.DoesNotExist:
        print(f"Sender {sender_id} not found")  # Debug
        return JsonResponse({'error': 'Sender not found'}, status=404)
    except Exception as e:
        print(f"Error: {str(e)}")  # Debug
        return JsonResponse({'error': str(e)}, status=500)

def validate_sender_name(sender_name, account_type):
    """Centralized validation function."""
    if not sender_name:
        return "Sender name cannot be empty."

    account_type = (account_type or "").strip().lower()
    if account_type == "transaction":
        if not re.fullmatch(r"[A-Za-z]+", sender_name):
            return "Sender name must contain only alphabets for Transaction accounts."
    elif account_type == "promotional":
        if not re.fullmatch(r"[0-9]+", sender_name):
            return "Sender name must contain only numbers for Promotional accounts."
    else:
        if not re.fullmatch(r"[A-Za-z0-9]+", sender_name):
            return "Sender name must be alphanumeric for other account types."
    return None

def add_sender(request):
    if request.method == 'POST':
        # Get current user as the sender owner
        current_user = request.user
        sender_name = request.POST.get('sender_name', '').strip()
        peid = request.POST.get('peid', '').strip()
        active_flag = request.POST.get('active_flag') == 'on'

        # Get user account type
        account_type = getattr(current_user, 'account_type', '') or getattr(current_user, 'sms_account_type', '') or ''

        # Validate sender name based on account type
        validation_error = validate_sender_name(sender_name, account_type)
        if validation_error:
            messages.error(request, f"❌ {validation_error}", extra_tags='sender')
            return redirect('sender_list')

        # Validate PEID is numeric
        if peid and not peid.isdigit():
            messages.error(request, "❌ PEID must contain only numbers.", extra_tags='sender')
            return redirect('sender_list')

        # Create sender record
        Sender.objects.create(
            user=current_user,
            sender_name=sender_name,
            peid=peid,
            active_flag=active_flag,
            start_date=timezone.now(),
            create_by=request.user.username if request.user.is_authenticated else "system",
        )

        messages.success(request, f"✅ Sender '{sender_name}' added successfully.", extra_tags='sender')
        return redirect('sender_list')

    return redirect('sender_list')

def edit_sender(request, sender_id):
    sender = get_object_or_404(Sender, pk=sender_id)

    if request.method == 'POST':
        # Keep the original user (not changeable)
        user = sender.user or request.user
        sender_name = request.POST.get('sender_name', '').strip()
        peid = request.POST.get('peid', '').strip()
        active_flag = request.POST.get('active_flag') == 'on'

        # Get user account type
        account_type = getattr(user, 'account_type', '') or getattr(user, 'sms_account_type', '') or ''

        # Validate sender name
        validation_error = validate_sender_name(sender_name, account_type)
        if validation_error:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': validation_error})
            messages.error(request, f"❌ {validation_error}", extra_tags='sender')
            return redirect('sender_list')

        # Validate PEID is numeric
        if peid and not peid.isdigit():
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'PEID must contain only numbers.'})
            messages.error(request, "❌ PEID must contain only numbers.", extra_tags='sender')
            return redirect('sender_list')

        # Update fields
        sender.sender_name = sender_name
        sender.peid = peid
        sender.active_flag = active_flag
        sender.last_updated_by = request.user.username if request.user.is_authenticated else "system"
        sender.last_updated_date = timezone.now()
        sender.save()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f"✅ Sender '{sender_name}' updated successfully."
            })
        
        messages.success(request, f"✏️ Sender '{sender_name}' updated successfully.", extra_tags='sender')
        return redirect('sender_list')

    return redirect('sender_list')

@login_required
def delete_sender(request, sender_id):
    sender = get_object_or_404(Sender, pk=sender_id)
    if request.method == 'POST':
        sender_name = sender.sender_name
        sender.delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'message': f"Sender '{sender_name}' deleted successfully."})
        messages.success(request, f"🗑️ Sender '{sender_name}' deleted successfully.", extra_tags='sender')
    return redirect('sender_list')

def download_sample_excel(request):
    """Download sample Excel template for bulk upload"""
    # Create sample data
    sample_data = [
        ['sender_name', 'peid'],
        ['TEST', '1234567890'],
        ['PROMO', '9876543210'],
    ]
    
    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sender_template.csv"'
    
    writer = csv.writer(response)
    writer.writerows(sample_data)
    
    return response

def import_senders(request):
    """Import senders from Excel/CSV file"""
    if request.method == 'POST' and request.FILES.get('excel_file'):
        excel_file = request.FILES['excel_file']
        # Use current user as the sender owner
        current_user = request.user
        account_type = getattr(current_user, 'account_type', '') or getattr(current_user, 'sms_account_type', '') or ''
        
        try:
            # Read the file
            if excel_file.name.endswith('.csv'):
                df = pd.read_csv(excel_file)
            else:
                df = pd.read_excel(excel_file)
            
            # Validate required columns
            required_columns = ['sender_name']
            for col in required_columns:
                if col not in df.columns:
                    messages.error(request, f"❌ Column '{col}' is required in the file.", extra_tags='sender')
                    return redirect('sender_list')
            
            success_count = 0
            error_count = 0
            errors = []
            
            for index, row in df.iterrows():
                sender_name = str(row.get('sender_name', '')).strip()
                peid = str(row.get('peid', '')).strip()
                
                # Skip empty rows
                if not sender_name:
                    continue
                
                # Validate sender name
                validation_error = validate_sender_name(sender_name, account_type)
                if validation_error:
                    errors.append(f"Row {index + 2}: {validation_error}")
                    error_count += 1
                    continue
                
                # Validate PEID if provided
                if peid and not peid.isdigit():
                    errors.append(f"Row {index + 2}: PEID must contain only numbers")
                    error_count += 1
                    continue
                
                # Create sender
                Sender.objects.create(
                    user=current_user,
                    sender_name=sender_name,
                    peid=peid,
                    active_flag=True,
                    start_date=timezone.now(),
                    create_by=request.user.username if request.user.is_authenticated else "system",
                )
                success_count += 1
            
            # Show results
            if success_count > 0:
                messages.success(request, f"✅ Successfully imported {success_count} senders.", extra_tags='sender')
            if errors:
                error_msg = "❌ Errors found:<br>" + "<br>".join(errors[:10])  # Show first 10 errors
                if len(errors) > 10:
                    error_msg += f"<br>... and {len(errors) - 10} more errors"
                messages.error(request, error_msg, extra_tags='sender')
            
        except Exception as e:
            messages.error(request, f"❌ Error reading file: {str(e)}", extra_tags='sender')
    
    return redirect('sender_list')
# ==================================================================================
from sms_app.models import User
import pandas as pd
import csv
import json

# Define valid DLT template types
VALID_DLT_TYPES = [
    'transactional',
    'promotional',
    'service_implicit',
    'service_explicit'
]

@login_required
def message_template_list(request):
    # Show only templates for the current logged-in user
    templates = get_accessible_templates_queryset(request.user)
    
    # Get only senders belonging to the current user
    user_senders = get_accessible_senders_queryset(request.user)
    
    # Check for template-specific messages and pass them to template
    template_messages = []
    if messages.get_messages(request):
        for message in messages.get_messages(request):
            if 'template' in message.extra_tags:
                template_messages.append({
                    'level': message.level,
                    'message': message.message,
                    'tags': message.tags
                })
    
    return render(request, 'message_templates.html', {
        'templates': templates,
        'user_senders': user_senders,
        'current_user': request.user,
        'template_messages': template_messages,
        'today': timezone.now().date(),
        'valid_dlt_types': VALID_DLT_TYPES
    })

@login_required
def add_message_template(request):
    if request.method == 'POST':
        # Auto-assign to logged-in user
        user = request.user
        sender_id = request.POST.get('sender_id') or None
        template_identifier = request.POST.get('template_identifier', '').strip()
        message_template = request.POST.get('message_template', '').strip()
        dlt_template_id = request.POST.get('dlt_template_id', '').strip()
        dlt_template_type = request.POST.get('dlt_template_type', '').strip().lower()
        start_date = request.POST.get('start_date') or timezone.now().date()
        end_date = request.POST.get('end_date') or None

        # Validate required fields - ONLY check if empty
        if not template_identifier:
            messages.error(request, "❌ Template identifier is required.", extra_tags='template')
            return redirect('message_template_list')
        
        if not message_template:
            messages.error(request, "❌ Message template content is required.", extra_tags='template')
            return redirect('message_template_list')
        
        # Validate DLT template type if provided
        if dlt_template_type and dlt_template_type not in VALID_DLT_TYPES:
            messages.error(request, f"❌ Invalid DLT template type. Must be one of: {', '.join(VALID_DLT_TYPES)}", extra_tags='template')
            return redirect('message_template_list')

        # Validate sender belongs to user
        if sender_id:
            try:
                sender = Sender.objects.get(pk=sender_id, user=user)
            except Sender.DoesNotExist:
                messages.error(request, "❌ Selected sender does not belong to your account.", extra_tags='template')
                return redirect('message_template_list')

        MessageTemplate.objects.create(
            user=user,
            sender_id=sender_id,
            template_identifier=template_identifier,
            message_template=message_template,
            dlt_template_id=dlt_template_id if dlt_template_id else None,
            dlt_template_type=dlt_template_type if dlt_template_type else None,
            start_date=start_date,
            end_date=end_date,
            create_by=request.user.username if request.user.is_authenticated else 'system',
        )

        messages.success(request, f"✅ Template '{template_identifier}' added successfully.", extra_tags='template')
        return redirect('message_template_list')
    return redirect('message_template_list')

@login_required
def edit_message_template(request, template_id):
    template = get_object_or_404(MessageTemplate, pk=template_id, user=request.user)

    if request.method == 'POST':
        # Keep original user (logged-in user)
        user = request.user
        sender_id = request.POST.get('sender_id') or None
        template_identifier = request.POST.get('template_identifier', '').strip()
        message_template = request.POST.get('message_template', '').strip()
        dlt_template_id = request.POST.get('dlt_template_id', '').strip()
        dlt_template_type = request.POST.get('dlt_template_type', '').strip().lower()
        start_date = request.POST.get('start_date') or template.start_date
        end_date = request.POST.get('end_date') or None

        # Validate required fields - ONLY check if empty
        if not template_identifier:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': "❌ Template identifier is required."})
            messages.error(request, "❌ Template identifier is required.", extra_tags='template')
            return redirect('message_template_list')
        
        if not message_template:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': "❌ Message template content is required."})
            messages.error(request, "❌ Message template content is required.", extra_tags='template')
            return redirect('message_template_list')
        
        # Validate DLT template type if provided
        if dlt_template_type and dlt_template_type not in VALID_DLT_TYPES:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': f"❌ Invalid DLT template type. Must be one of: {', '.join(VALID_DLT_TYPES)}"})
            messages.error(request, f"❌ Invalid DLT template type. Must be one of: {', '.join(VALID_DLT_TYPES)}", extra_tags='template')
            return redirect('message_template_list')

        # Validate sender belongs to user if provided
        if sender_id:
            try:
                sender = Sender.objects.get(pk=sender_id, user=user)
            except Sender.DoesNotExist:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'message': "❌ Selected sender does not belong to your account."})
                messages.error(request, "❌ Selected sender does not belong to your account.", extra_tags='template')
                return redirect('message_template_list')

        template.user = user
        template.sender_id = sender_id
        template.template_identifier = template_identifier
        template.message_template = message_template
        template.dlt_template_id = dlt_template_id if dlt_template_id else None
        template.dlt_template_type = dlt_template_type if dlt_template_type else None
        template.start_date = start_date
        template.end_date = end_date
        template.last_updated_by = request.user.username if request.user.is_authenticated else "system"
        template.last_updated_date = timezone.now()
        template.save()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': f"✏️ Template '{template.template_identifier}' updated successfully."
            })
        
        messages.success(request, f"✏️ Template '{template.template_identifier}' updated successfully.", extra_tags='template')
        return redirect('message_template_list')

    # If it's a GET request, redirect to the list page
    return redirect('message_template_list')

@login_required
def get_template(request, template_id):
    """Get template data for AJAX editing"""
    try:
        template = MessageTemplate.objects.get(pk=template_id, user=request.user)
        
        data = {
            'template_id': template.id,
            'user_id': template.user_id,
            'user_name': template.user.username if template.user else '',
            'sender_id': template.sender_id,
            'sender_name': template.sender.sender_name if template.sender else '',
            'template_identifier': template.template_identifier,
            'message_template': template.message_template,
            'dlt_template_id': template.dlt_template_id or '',
            'dlt_template_type': template.dlt_template_type or '',
            'start_date': template.start_date.strftime('%Y-%m-%d') if template.start_date else '',
            'end_date': template.end_date.strftime('%Y-%m-%d') if template.end_date else '',
        }
        
        return JsonResponse(data)
    except MessageTemplate.DoesNotExist:
        return JsonResponse({'error': 'Template not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
def download_template_sample(request):
    """Download sample Excel template for bulk upload"""
    # Create sample data
    sample_data = [
        ['template_identifier', 'message_template', 'senders', 'dlt_template_id', 'dlt_template_type'],
        ['WELCOME_MSG', 'Welcome to our service!', 'SENDER1', 'T123456', 'transactional'],
        ['OTP_VERIFY', 'Your OTP is {OTP}', 'SENDER1, SENDER2', 'T789012', 'transactional'],
        ['ORDER_CONFIRM', 'Your order #{ORDER_ID} has been confirmed', 'SENDER2,SENDER3', 'T345678', 'transactional'],
        ['PROMO_OFFER', 'Special offer: {OFFER_DETAILS}', 'ALL_SENDERS', 'P901234', 'promotional'],
        ['REMINDER', 'Reminder: {EVENT_NAME} is tomorrow', '', 'T567890', 'service_implicit'],
        ['SERVICE_UPDATE', 'Service update: {UPDATE_INFO}', 'SENDER1', 'S123456', 'service_explicit'],
    ]
    
    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="message_template_template.csv"'
    
    writer = csv.writer(response)
    writer.writerows(sample_data)
    
    return response

@login_required
def import_templates(request):
    """Import templates from Excel/CSV file with multiple sender support"""
    if request.method == 'POST' and request.FILES.get('template_file'):
        excel_file = request.FILES['template_file']
        
        try:
            # Read the file
            if excel_file.name.endswith('.csv'):
                df = pd.read_csv(excel_file)
            else:
                df = pd.read_excel(excel_file)
            
            # Validate required columns
            required_columns = ['template_identifier', 'message_template']
            for col in required_columns:
                if col not in df.columns:
                    messages.error(request, f"❌ Column '{col}' is required in the file.", extra_tags='template')
                    return redirect('message_template_list')
            
            success_count = 0
            error_count = 0
            errors = []
            warnings = []
            
            # Get all senders for the current user
            user_senders = Sender.objects.filter(user=request.user)
            sender_name_to_id = {sender.sender_name.strip().lower(): sender.sender_id for sender in user_senders}
            
            for index, row in df.iterrows():
                template_identifier = str(row.get('template_identifier', '')).strip()
                message_template = str(row.get('message_template', '')).strip()
                senders_input = str(row.get('senders', '')).strip()
                dlt_template_id = str(row.get('dlt_template_id', '')).strip()
                dlt_template_type = str(row.get('dlt_template_type', '')).strip().lower()
                
                # Skip empty rows
                if not template_identifier or not message_template:
                    errors.append(f"Row {index + 2}: Template identifier and message template are required")
                    error_count += 1
                    continue
                
                # Validate DLT template type if provided
                if dlt_template_type and dlt_template_type not in VALID_DLT_TYPES:
                    errors.append(f"Row {index + 2}: Invalid DLT template type '{dlt_template_type}'. Must be one of: {', '.join(VALID_DLT_TYPES)}")
                    error_count += 1
                    continue
                
                # Process senders
                sender_ids = []
                unmatched_senders = []
                
                if senders_input:
                    # Split by comma and clean up
                    sender_names = [name.strip() for name in senders_input.split(',') if name.strip()]
                    
                    for sender_name in sender_names:
                        # Try exact match first
                        if sender_name.lower() in sender_name_to_id:
                            sender_ids.append(sender_name_to_id[sender_name.lower()])
                        else:
                            # Try case-insensitive partial match
                            matched = False
                            for db_sender_name, sender_id in sender_name_to_id.items():
                                if sender_name.lower() == db_sender_name.lower():
                                    sender_ids.append(sender_id)
                                    matched = True
                                    break
                            
                            if not matched:
                                unmatched_senders.append(sender_name)
                
                # If no senders specified or "ALL_SENDERS" keyword used
                if senders_input.upper() == 'ALL_SENDERS' or senders_input == '*':
                    # Use all user's senders
                    sender_ids = list(sender_name_to_id.values())
                elif not senders_input:
                    # No sender specified - template will be created without sender
                    sender_ids = [None]
                
                # If there are unmatched senders, add warning but continue
                if unmatched_senders:
                    warnings.append(f"Row {index + 2}: Senders not found - {', '.join(unmatched_senders)}")
                
                # If no valid senders found and not using ALL_SENDERS, skip
                if not sender_ids and senders_input and senders_input.upper() != 'ALL_SENDERS':
                    errors.append(f"Row {index + 2}: No valid senders found. Available senders: {', '.join(sender_name_to_id.keys())}")
                    error_count += 1
                    continue
                
                # Create template for each sender (or one without sender if None)
                for sender_id in sender_ids:
                    MessageTemplate.objects.create(
                        user=request.user,
                        sender_id=sender_id,
                        template_identifier=template_identifier,
                        message_template=message_template,
                        dlt_template_id=dlt_template_id if dlt_template_id else None,
                        dlt_template_type=dlt_template_type if dlt_template_type else None,
                        start_date=timezone.now().date(),
                        create_by=request.user.username if request.user.is_authenticated else "system",
                    )
                    success_count += 1
            
            # Show results
            result_messages = []
            
            if success_count > 0:
                result_messages.append(f"✅ Successfully imported {success_count} template records.")
            
            if warnings:
                warning_msg = "⚠️ Warnings:<br>" + "<br>".join(warnings[:10])
                if len(warnings) > 10:
                    warning_msg += f"<br>... and {len(warnings) - 10} more warnings"
                result_messages.append(warning_msg)
            
            if errors:
                error_msg = "❌ Errors found:<br>" + "<br>".join(errors[:10])
                if len(errors) > 10:
                    error_msg += f"<br>... and {len(errors) - 10} more errors"
                result_messages.append(error_msg)
            
            # Show available senders if there were errors/warnings
            if errors or warnings:
                available_senders = list(sender_name_to_id.keys())
                if available_senders:
                    result_messages.append(f"📋 Available senders: {', '.join(available_senders)}")
                else:
                    result_messages.append("📋 No senders found in your account. Please add senders first.")
            
            # Combine all messages
            if result_messages:
                messages.success(request, "<br>".join(result_messages), extra_tags='template')
            
        except Exception as e:
            messages.error(request, f"❌ Error reading file: {str(e)}", extra_tags='template')
    
    return redirect('message_template_list')
# =========================
#  group controller views.py
from .models import GroupHeader, GroupLine

# ✅ List all groups
def group_list(request):
    groups = GroupHeader.objects.filter(user=request.user)
    return render(request, 'group_list.html', {'groups': groups})

# ✅ Create new group (with multiple contacts)
def group_create(request):
    if request.method == 'POST':
        group_name = request.POST.get('group_name')
        numbers = request.POST.getlist('mobile_number[]')
        names = request.POST.getlist('contact_name[]')

        group = GroupHeader.objects.create(
            user=request.user,
            group_name=group_name,
            create_by=request.user.username,
        )

        # Create GroupLine entries
        for i, num in enumerate(numbers):
            if num.strip():
                GroupLine.objects.create(
                    group=group,
                    contact_name=names[i] if i < len(names) else '',
                    mobile_number=num,
                    create_by=request.user.username,
                )

        messages.success(request, "Group created successfully!")
        return redirect('group_list')

    return redirect('group_list')

# ✅ Update existing group
def group_update(request, pk):
    group = get_object_or_404(GroupHeader, pk=pk)
    if request.method == 'POST':
        group.group_name = request.POST.get('group_name')
        group.last_updated_by = request.user.username
        group.last_updated_date = timezone.now()
        group.save()

        # Delete old lines and recreate
        group.lines.all().delete()

        numbers = request.POST.getlist('mobile_number[]')
        names = request.POST.getlist('contact_name[]')

        for i, num in enumerate(numbers):
            if num.strip():
                GroupLine.objects.create(
                    group=group,
                    contact_name=names[i] if i < len(names) else '',
                    mobile_number=num,
                    create_by=request.user.username,
                )

        messages.success(request, "Group updated successfully!")
        return redirect('group_list')

    return redirect('group_list')

# ✅ Delete group
def group_delete(request, pk):
    group = get_object_or_404(GroupHeader, pk=pk)
    group.delete()
    messages.success(request, "Group deleted successfully!")
    return redirect('group_list')


@login_required
def user_list(request):
    user = request.user
    user_company = getattr(user, 'company', None)
    
    # Get companies based on user role
    companies = Company.objects.all()
    # ----------------------------
    # STEP 1: Company-wise roles
    # ----------------------------
    if user.is_superuser:
        # Django superuser sees all roles from all companies
        company_roles = Role.objects.filter(flag=True)
    else:
        # Normal users only see roles of their company
        company_roles = Role.objects.filter(
            company=user_company,
            flag=True
        )

    # ----------------------------
    # STEP 2: Role hierarchy
    # ----------------------------
    if user.is_superuser:
        available_roles = company_roles

    elif user.role:
        role_name = user.role.role_name

        if role_name == 'Super Admin':
            available_roles = company_roles.filter(
                role_name='Support Admin'
            )

        elif role_name == 'Support Admin':
            available_roles = company_roles.filter(
                role_name__in=['Reseller', 'Client']
            )

        elif role_name == 'Reseller':
            available_roles = company_roles.filter(
                role_name='Client'
            )

        else:
            available_roles = Role.objects.none()

    else:
        available_roles = Role.objects.none()


    # Default empty queryset
    users = User.objects.none()

    if user.is_superuser:
        users = User.objects.select_related('company', 'role') \
                            .exclude(is_superuser=True) \
                            .order_by('-join_date')

    elif user.role:
        role_name = user.role.role_name

        if role_name == 'Super Admin':
            # Super Admin → can see all users
            # users = User.objects.select_related('company', 'role').all().order_by('-join_date')
            users = User.objects.select_related('company', 'role') \
                                .filter(create_by=user.username) \
                                .order_by('-join_date')

        elif role_name == 'Support Admin':
            # Support Admin → only users created by them
            users = User.objects.select_related('company', 'role') \
                                .filter(create_by=user.username) \
                                .order_by('-join_date')

        elif role_name == 'Reseller':
            # Reseller → only users they created (usually Clients)
            users = User.objects.select_related('company', 'role') \
                                .filter(create_by=user.username) \
                                .order_by('-join_date')

        elif role_name == 'Client':
            # Client → should only see themselves, or be redirected
            messages.warning(request, "You do not have permission to view this page.")
            return redirect('dashboard')  # Replace 'dashboard' with your main view name

    return render(request, 'user_management.html', {
        'users': users,
        'companies': companies,
        'available_roles': available_roles,  # Changed from 'roles' to 'available_roles'
        'user_company': user_company,
        'current_user': user,  # Pass current user to template
    })

@login_required
def add_user(request):
    if request.method != "POST":
        return redirect('user_list')

    current_user = request.user
    data = request.POST

    # =========================
    # Basic fields
    # =========================
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    confirm_password = data.get('confirm_password', '')
    full_name = data.get('full_name', '').strip()
    mobile_number = data.get('mobile_number', '').strip()
    tfa_number = data.get('tfa_number', '').strip()
    address = data.get('address', '').strip()
    pin_code = data.get('pin_code', '').strip()
    country = data.get('country', '').strip()
    region = data.get('region', '').strip()
    city = data.get('city', '').strip()
    sms_account_type = data.get('sms_account_type')
    is_active = True  # default on add

    role_id = data.get('role_id')

    # =========================
    # Company logic (FIXED)
    # =========================
    if current_user.is_superuser:
        # ONLY Django superuser can select company
        company_id = data.get('company_id')
    else:
        # Everyone else → auto-assign own company
        company_id = getattr(current_user.company, 'company_id', None)

    # =========================
    # Validations (KEPT - for security)
    # =========================
    if not username or not email:
        messages.error(request, "Username and Email are required.")
        return redirect('user_list')

    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, "Enter a valid email address.")
        return redirect('user_list')

    # =========================
    # Role validation
    # =========================
    if not role_id:
        messages.error(request, "Role is required.")
        return redirect('user_list')

    try:
        selected_role = Role.objects.get(pk=role_id)

        if current_user.is_superuser:
            pass  # Full access

        elif current_user.role:
            role_name = current_user.role.role_name

            if role_name == 'Super Admin' and selected_role.role_name != 'Support Admin':
                messages.error(request, "You can only create Support Admin users.")
                return redirect('user_list')

            elif role_name == 'Support Admin' and selected_role.role_name not in ['Reseller', 'Client']:
                messages.error(request, "You can only create Reseller or Client users.")
                return redirect('user_list')

            elif role_name == 'Reseller' and selected_role.role_name != 'Client':
                messages.error(request, "You can only create Client users.")
                return redirect('user_list')

            elif role_name == 'Client':
                messages.error(request, "You don't have permission to create users.")
                return redirect('user_list')
        else:
            messages.error(request, "You don't have a valid role assigned.")
            return redirect('user_list')

    except Role.DoesNotExist:
        messages.error(request, "Invalid role selected.")
        return redirect('user_list')

    # =========================
    # Company validation (STRICT)
    # =========================
    if not company_id:
        messages.error(request, "Company is required.")
        return redirect('user_list')

    try:
        selected_company = Company.objects.get(pk=company_id)

        if not current_user.is_superuser:
            if current_user.company.company_id != selected_company.company_id:
                messages.error(
                    request,
                    "You can only create users for your own company."
                )
                return redirect('user_list')

    except Company.DoesNotExist:
        messages.error(request, "Invalid company selected.")
        return redirect('user_list')

    # =========================
    # Uniqueness checks (KEPT - server-side validation)
    # =========================
    if User.objects.filter(username=username).exists():
        messages.error(request, "Username already exists.")
        return redirect('user_list')

    if User.objects.filter(email=email).exists():
        messages.error(request, "Email already exists.")
        return redirect('user_list')

    # =========================
    # Create user
    # =========================
    try:
        user = User(
            username=username,
            email=email,
            full_name=full_name or None,
            mobile_number=mobile_number or None,
            tfa_number=tfa_number or None,
            address=address or None,
            pin_code=pin_code or None,
            country=country or None,
            region=region or None,
            city=city or None,
            sms_account_type=sms_account_type or None,
            is_active=is_active,
            create_by=current_user.username,
            last_updated_by=current_user.username,
            company=selected_company,
            role=selected_role,
        )

        user.set_password(password)
        user.save()

        messages.success(
            request,
            f"User '{username}' created successfully with {selected_role.role_name} role."
        )

    except IntegrityError:
        messages.error(request, "Username or email already exists.")
    except Exception as e:
        messages.error(request, f"Error creating user: {str(e)}")
        if settings.DEBUG:
            raise e

    return redirect('user_list')

@login_required
def edit_user(request, user_id):
    target = get_object_or_404(User, pk=user_id)

    if request.method != "POST":
        return redirect('user_list')

    data = request.POST
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    full_name = data.get('full_name', '').strip()
    mobile_number = data.get('mobile_number', '').strip()
    tfa_number = data.get('tfa_number', '').strip()
    pin_code = data.get('pin_code', '').strip()
    address = data.get('address', '').strip()
    company_id = data.get('company_id') or None
    role_id = data.get('role_id') or None
    country = data.get('country', '').strip()
    region = data.get('region', '').strip()
    city = data.get('city', '').strip()
    sms_account_type = data.get('sms_account_type', None)
    is_active = True if data.get('is_active') else False
    password = data.get('password', '').strip()

    # Basic validations
    if not username or not email:
        messages.error(request, "Username and Email are required.")
        return redirect('user_list')

    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, "Enter a valid email address.")
        return redirect('user_list')

    # check uniqueness if changed
    if target.username != username and User.objects.filter(username=username).exclude(pk=target.pk).exists():
        messages.error(request, "Username already taken.")
        return redirect('user_list')
    if target.email != email and User.objects.filter(email=email).exclude(pk=target.pk).exists():
        messages.error(request, "Email already taken.")
        return redirect('user_list')

    # update fields
    target.username = username
    target.email = email
    target.full_name = full_name or None
    target.mobile_number = mobile_number or None
    target.tfa_number = tfa_number or None
    target.pin_code = pin_code or None
    target.address = address or None
    target.country = country or None
    target.region = region or None
    target.city = city or None
    target.sms_account_type = sms_account_type or None
    target.is_active = is_active
    target.last_updated_by = getattr(request.user, 'username', '') or ''

    if company_id:
        try:
            target.company = Company.objects.get(pk=company_id)
        except Company.DoesNotExist:
            target.company = None
    else:
        target.company = None

    if role_id:
        try:
            target.role = Role.objects.get(pk=role_id)
        except Role.DoesNotExist:
            target.role = None
    else:
        target.role = None

    # password update if provided
    # if password:
    #     ok, pw_msg = _validate_password_strength(password)
    #     if not ok:
    #         messages.error(request, pw_msg)
    #         return redirect('user_list')
    #     target.set_password(password)

    # try:
    #     target.save()
    #     messages.success(request, f"User '{target.username}' updated.")
    # except Exception as e:
    #     messages.error(request, f"Error saving user: {str(e)}")

    return redirect('user_list')


def user_detail(request, user_id):
    user = get_object_or_404(User.objects.select_related('company', 'role'), pk=user_id)
    return render(request, 'user_detail.html', {'user': user})


from django.http import JsonResponse
from .models import User, Company, Role

def user_detail(request, user_id):
    user = User.objects.get(user_id=user_id)
    companies = Company.objects.all()
    roles = Role.objects.all()
    return render(request, 'user_detail.html', {
        'user': user,
        'companies': companies,
        'roles': roles
    })

def update_user_detail(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(User, pk=user_id)
        try:
            expiry_date = request.POST.get('expiry_date') or None  # ✅ handle blank safely
            if expiry_date:
                user.expiry_date = expiry_date
            else:
                user.expiry_date = None  # ✅ set NULL if not selected

            user.username = request.POST.get('username', user.username)
            user.full_name = request.POST.get('full_name', user.full_name)
            user.email = request.POST.get('email', user.email)
            user.mobile_number = request.POST.get('mobile_number', user.mobile_number)
            user.tfa_number = request.POST.get('tfa_number', user.tfa_number)
            user.pin_code = request.POST.get('pin_code', user.pin_code)
            user.country = request.POST.get('country', user.country)
            user.region = request.POST.get('region', user.region)
            user.city = request.POST.get('city', user.city)
            user.address = request.POST.get('address', user.address)

            user.company_id = request.POST.get('company') or None
            user.role_id = request.POST.get('role') or None
            user.sms_account_type = request.POST.get('sms_account_type') or None

            user.smpp_enabled = request.POST.get('smpp_enabled') == 'True'
            user.smpp_bind_mode = request.POST.get('smpp_bind_mode') or None
            user.smpp_port = request.POST.get('smpp_port') or None
            user.smpp_sessions = request.POST.get('smpp_sessions') or None
            user.smpp_tps = request.POST.get('smpp_tps') or None
            user.sms_posting_start_hour = request.POST.get('sms_posting_start_hour') or None
            user.sms_posting_end_hour = request.POST.get('sms_posting_end_hour') or None

            user.last_updated_by = request.user.username
            user.save()
            return JsonResponse({'success': True})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})




# ======================================================================================
from django.db import transaction
from .models import SmppConnection


# def smpp_form(request):
#     smpp_connections = SmppConnection.objects.all().order_by('-created_date')
#     return render(request, 'smpp_form.html', {"smpp_connections": smpp_connections})
import pytz

def smpp_form(request):
    smpp_connections = SmppConnection.objects.all().order_by('-created_date')
    timezones = pytz.common_timezones   # or pytz.all_timezones

    return render(request, 'smpp_form.html', {
        "smpp_connections": smpp_connections,
        "timezones": timezones
    })



def smpp_view_list(request):
    smpp_connections = SmppConnection.objects.all().order_by('-created_date')
    return render(request, 'smpp_view_list.html', {"smpp_connections": smpp_connections})


# ---------------------------------------------------------------- #
# ADD SMPP
# ---------------------------------------------------------------- #
from pytz import all_timezones
from django.http import JsonResponse
from django.contrib import messages
from django.db import transaction
from django.shortcuts import render
import json

def to_int(value, default=0):
    """Safely convert string to integer"""
    try:
        return int(value) if value else default
    except:
        return default

@transaction.atomic
def add_smpp(request):
    if request.method == 'POST':
        try:
            # Check if it's an AJAX request
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            
            # Print debug info for non-AJAX requests
            if not is_ajax:
                print("\n===== DEBUGGING POST DATA =====")
                for k, v in request.POST.items():
                    print(f"{k} = {v}")
                print("================================\n")

            # Extract and validate required fields
            connect_name = request.POST.get("connect_name")
            ip = request.POST.get("ip")
            username = request.POST.get("username")
            password = request.POST.get("password")
            bind_type = request.POST.get("bind_type")
            
            # Validate required fields
            if not all([connect_name, ip, username, password, bind_type]):
                if is_ajax:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'All required fields must be filled'
                    })
                else:
                    messages.error(request, "❌ Please fill all required fields")
                    return render(request, "smpp_form.html", {
                        "timezones": all_timezones,
                    })

            # Create SMPP connection
            smpp = SmppConnection(
                # Text fields
                connect_name=connect_name,
                billing_method=request.POST.get("billing_method"),
                account_type=request.POST.get("account_type"),
                error_code_map=request.POST.get("error_code_map"),
                smpp_connect_status=request.POST.get("smpp_connect_status") or "active",

                ip=ip,
                username=username,
                password=password,
                bind_type=bind_type,
                system_type=request.POST.get("system_type"),

                # Integer fields (Safe Conversion)
                tps=to_int(request.POST.get("tps")),
                tr_trx_port=to_int(request.POST.get("tr_trx_port")),
                tx_sessions=to_int(request.POST.get("tx_sessions")),
                rx_port=to_int(request.POST.get("rx_port")),
                rx_sessions=to_int(request.POST.get("rx_sessions")),

                # TON/NPI integers
                dest_addr_ton=to_int(request.POST.get("dest_addr_ton")),
                dest_addr_npi=to_int(request.POST.get("dest_addr_npi")),
                source_addr_ton=to_int(request.POST.get("source_addr_ton")),
                source_addr_npi=to_int(request.POST.get("source_addr_npi")),

                # Timezone
                delivery_report_timezone=request.POST.get("delivery_report_timezone"),
                override_delivery_time_flag=request.POST.get("override_delivery_time_flag"),

                # Boolean fields
                support_message_validity=('support_message_validity' in request.POST),
                dlt_enable=('dlt_enable' in request.POST),
                send_dlt_template_id=('send_dlt_template_id' in request.POST),
                reject_non_dlt_template_message=('reject_non_dlt_template_message' in request.POST),

                # DLR Format
                date_format_in_dlr=request.POST.get("date_format_in_dlr"),
                modify_time_zone=request.POST.get("modify_time_zone"),

                # DLT fields with default values
                dlt_telemarketer_id=request.POST.get("dlt_telemarketer_id"),
                dlt_entity_id_tag=request.POST.get("dlt_entity_id_tag") or "tag_1400",
                dlt_template_id_tag=request.POST.get("dlt_template_id_tag") or "tag_1401",
                dlt_telemarketer_id_tag=request.POST.get("dlt_telemarketer_id_tag") or "tag_1402",
                dlt_telemarketer_tag_behaviour=request.POST.get("dlt_telemarketer_tag_behaviour"),

                # Attributes
                attributes_1=request.POST.get("attributes_1"),
                attributes_2=request.POST.get("attributes_2"),
                attributes_3=request.POST.get("attributes_3"),
                attributes_4=request.POST.get("attributes_4"),
                attributes_5=request.POST.get("attributes_5"),
                attributes_6=request.POST.get("attributes_6"),
                attributes_7=request.POST.get("attributes_7"),
                attributes_8=request.POST.get("attributes_8"),
                attributes_9=request.POST.get("attributes_9"),
                attributes_10=request.POST.get("attributes_10"),

                # Metadata
                create_by=request.user.username if request.user.is_authenticated else "System",
                last_updated_by=request.user.username if request.user.is_authenticated else "System",
            )

            smpp.save()

            # Return appropriate response
            if is_ajax:
                return JsonResponse({
                    'status': 'success',
                    'message': f"SMPP Connection '{smpp.connect_name}' added successfully.",
                    'data': {
                        'id': smpp.id,
                        'name': smpp.connect_name,
                        'ip': smpp.ip,
                        'bind_type': smpp.bind_type
                    }
                })
            else:
                messages.success(request, f"✅ SMPP Connection '{smpp.connect_name}' added successfully.")
                # Redirect to connections list after success
                return redirect('smpp_form')  # Change this to your connections list URL

        except Exception as e:
            print("\n❌ ERROR OCCURRED WHILE SAVING SMPP")
            print(e)
            print("====================================\n")

            if is_ajax:
                return JsonResponse({
                    'status': 'error',
                    'message': f"Error adding SMPP Connection: {str(e)}"
                })
            else:
                messages.error(request, f"❌ Error adding SMPP Connection: {str(e)}")

    # GET request or error - show form
    return render(request, "smpp_form.html", {
        "timezones": all_timezones,
    })
# ---------------------------------------------------------------- #
# EDIT SMPP
# ---------------------------------------------------------------- #

@transaction.atomic
def edit_smpp(request, smpp_id):
    smpp = get_object_or_404(SmppConnection, pk=smpp_id)

    if request.method == "POST":
        try:
            # Normal fields
            basic_fields = [
                "connect_name", "billing_method", "account_type", "error_code_map",
                "smpp_connect_status", "ip", "username", "password", "bind_type",
                "system_type", "dest_addr_ton", "dest_addr_npi", "source_addr_ton",
                "source_addr_npi", "delivery_report_timezone", "date_format_in_dlr",
                "modify_time_zone", "dlt_telemarketer_id", "dlt_entity_id_tag",
                "dlt_template_id_tag", "dlt_telemarketer_id_tag",
                "dlt_telemarketer_tag_behaviour",
                "attributes_1", "attributes_2", "attributes_3", "attributes_4",
                "attributes_5", "attributes_6", "attributes_7", "attributes_8",
                "attributes_9", "attributes_10"
            ]

            for field in basic_fields:
                value = request.POST.get(field)
                setattr(smpp, field, value)

            # Integer fields safely
            smpp.tps = int(request.POST.get("tps")) if request.POST.get("tps") else None
            smpp.tr_trx_port = int(request.POST.get("tr_trx_port")) if request.POST.get("tr_trx_port") else None
            smpp.tx_sessions = int(request.POST.get("tx_sessions")) if request.POST.get("tx_sessions") else None
            smpp.rx_port = int(request.POST.get("rx_port")) if request.POST.get("rx_port") else None
            smpp.rx_sessions = int(request.POST.get("rx_sessions")) if request.POST.get("rx_sessions") else None

            # Boolean fields
            smpp.support_message_validity = ('support_message_validity' in request.POST)
            smpp.dlt_enable = ('dlt_enable' in request.POST)
            smpp.send_dlt_template_id = ('send_dlt_template_id' in request.POST)
            smpp.reject_non_dlt_template_message = ('reject_non_dlt_template_message' in request.POST)

            # Override time fields
            smpp.override_delivery_time_flag = request.POST.get("override_delivery_time_flag")

            if smpp.override_delivery_time_flag == "yes":
                time_str = request.POST.get("override_delivery_time")
                try:
                    smpp.override_delivery_time = (
                        timezone.datetime.strptime(time_str, "%H:%M").time()
                        if time_str else None
                    )
                except ValueError:
                    smpp.override_delivery_time = None
            else:
                smpp.override_delivery_time = None

            smpp.last_updated_by = request.user.username if request.user.is_authenticated else "System"
            smpp.last_updated_date = timezone.now()

            smpp.save()
            messages.success(request, f"✅ SMPP Connection '{smpp.connect_name}' updated successfully.")

        except Exception as e:
            messages.error(request, f"❌ Error updating SMPP: {e}")

    return redirect("smpp_list")


# ---------------------------------------------------------------- #
# DELETE SMPP
# ---------------------------------------------------------------- #

@transaction.atomic
def delete_smpp(request, smpp_id):
    smpp = get_object_or_404(SmppConnection, pk=smpp_id)

    try:
        smpp.delete()
        messages.success(request, f"🗑️ SMPP Connection '{smpp.connect_name}' deleted successfully.")
    except Exception as e:
        messages.error(request, f"❌ Error deleting SMPP: {e}")

    return redirect('smpp_list')

# routing controller views.py

from django.db import transaction
from django.urls import reverse
from .models import Routing, RoutingLine, SmppConnection
from django.http import JsonResponse

@transaction.atomic
def routing_list(request):
    """
    Show list of routings and render modals for add/edit.
    """
    routings = Routing.objects.all().order_by('-created_date')
    smpp_connections = SmppConnection.objects.filter(smpp_connect_status__iexact="Active").order_by('connect_name')
    user_company = None
    if hasattr(request, "user") and request.user.is_authenticated:
        user_company = getattr(request.user, "company", None)

    return render(request, 'routing_list.html', {
        'routings': routings,
        'smpp_connections': smpp_connections,
        'user_company': user_company,
    })

def _parse_checkbox(post, key):
    return True if post.get(key) == 'on' else False

@transaction.atomic
def add_routing(request):
    if request.method != 'POST':
        messages.error(request, "❌ Invalid request method.")
        return redirect('routing_list')

    try:
        name = request.POST.get('name', '').strip()
        strategy = request.POST.get('strategy', 'Dedicated')
        primary_connect = request.POST.get('primary_connect', '').strip() or None

        apply_rules = _parse_checkbox(request.POST, 'apply_rules')
        message_retry = _parse_checkbox(request.POST, 'message_retry')
        reject_non_peid_messages = _parse_checkbox(request.POST, 'reject_non_peid_messages')

        created_by = ''
        if request.user and request.user.is_authenticated:
            created_by = request.user.username

        routing = Routing.objects.create(
            name=name,
            strategy=strategy,
            primary_connect=primary_connect,
            apply_rules=apply_rules,
            message_retry=message_retry,
            reject_non_peid_messages=reject_non_peid_messages,
            create_by=created_by,
            created_date=timezone.now()
        )

        # --- Save RoutingLine(s) based on strategy ---
        _save_routing_lines_from_post(request.POST, routing)

        messages.success(request, f"✅ Routing '{routing.name}' added successfully.")
    except Exception as e:
        transaction.set_rollback(True)
        messages.error(request, f"❌ Error while adding routing: {e}")

    return redirect('routing_list')

@transaction.atomic
def edit_routing(request, rout_id):
    if request.method != 'POST':
        messages.error(request, "❌ Invalid request method.")
        return redirect('routing_list')

    routing = get_object_or_404(Routing, rout_id=rout_id)

    try:
        routing.name = request.POST.get('name', routing.name).strip()
        routing.strategy = request.POST.get('strategy', routing.strategy)
        routing.primary_connect = request.POST.get('primary_connect', routing.primary_connect).strip() or None

        routing.apply_rules = _parse_checkbox(request.POST, 'apply_rules')
        routing.message_retry = _parse_checkbox(request.POST, 'message_retry')
        routing.reject_non_peid_messages = _parse_checkbox(request.POST, 'reject_non_peid_messages')

        if request.user and request.user.is_authenticated:
            routing.last_updated_by = request.user.username
        routing.last_updated_date = timezone.now()

        routing.save()

        # Remove existing lines and re-create according to posted values
        routing.routing_lines.all().delete()
        _save_routing_lines_from_post(request.POST, routing)

        messages.success(request, f"✅ Routing '{routing.name}' updated successfully.")
    except Exception as e:
        transaction.set_rollback(True)
        messages.error(request, f"❌ Error while updating routing: {e}")

    return redirect('routing_list')

def _save_routing_lines_from_post(post, routing):
    """
    Parse POST payload and create RoutingLine rows for the given routing.
    Expected input shapes:
      - Dedicated: smpp_dedicated (single smpp_id)
      - Round-Robin: smpp_roundrobin[] (multiple smpp_id)
      - Percentage: percentage_smpp[] and percentage_weight[] (parallel arrays)
      - Priority: priority_smpp[] and priority_value[] (parallel arrays)
    """
    strategy = post.get('strategy', 'Dedicated')

    # Helper: safe get smpp object
    def get_smpp_or_none(smpp_id):
        try:
            return SmppConnection.objects.get(pk=int(smpp_id))
        except Exception:
            return None

    created_by = ''
    if hasattr(post, '_request') and getattr(post._request, 'user', None) and post._request.user.is_authenticated:
        created_by = post._request.user.username

    # If strategy is Dedicated
    if strategy == 'Dedicated':
        smpp_id = post.get('smpp_dedicated')
        if smpp_id:
            smpp_obj = get_smpp_or_none(smpp_id)
            if smpp_obj:
                RoutingLine.objects.create(
                    rout=routing,
                    smpp=smpp_obj,
                    percentage_weight=100,
                    start_date=post.get('ded_start_date') or None,
                    end_date=post.get('ded_end_date') or None,
                    create_by=created_by
                )

    # Round-Robin: multiple smpp ids (equal round robin)
    elif strategy == 'Round-Robin':
        smpp_ids = post.getlist('smpp_roundrobin[]') or post.getlist('smpp_roundrobin') or []
        for smpp_id in smpp_ids:
            smpp_obj = get_smpp_or_none(smpp_id)
            if smpp_obj:
                RoutingLine.objects.create(
                    rout=routing,
                    smpp=smpp_obj,
                    percentage_weight=None,
                    create_by=created_by
                )

    # Percentage: parallel lists
    elif strategy == 'Percentage':
        smpp_ids = post.getlist('percentage_smpp[]') or post.getlist('percentage_smpp') or []
        weights = post.getlist('percentage_weight[]') or post.getlist('percentage_weight') or []
        # ensure equal length
        for idx, smpp_id in enumerate(smpp_ids):
            if not smpp_id:
                continue
            smpp_obj = get_smpp_or_none(smpp_id)
            try:
                weight_val = int(weights[idx]) if idx < len(weights) and weights[idx] != '' else 0
            except Exception:
                weight_val = 0
            if smpp_obj:
                RoutingLine.objects.create(
                    rout=routing,
                    smpp=smpp_obj,
                    percentage_weight=weight_val,
                    create_by=created_by
                )

    # Priority: parallel lists
    elif strategy == 'Priority':
        smpp_ids = post.getlist('priority_smpp[]') or post.getlist('priority_smpp') or []
        priorities = post.getlist('priority_value[]') or post.getlist('priority_value') or []
        for idx, smpp_id in enumerate(smpp_ids):
            if not smpp_id:
                continue
            smpp_obj = get_smpp_or_none(smpp_id)
            try:
                prio_val = int(priorities[idx]) if idx < len(priorities) and priorities[idx] != '' else 0
            except Exception:
                prio_val = 0
            if smpp_obj:
                RoutingLine.objects.create(
                    rout=routing,
                    smpp=smpp_obj,
                    percentage_weight=prio_val,  # store priority in percentage_weight column (reused)
                    create_by=created_by
                )

    # If none matched, do nothing. Additional attributes can be parsed similarly.
# ================================================================================



from .models import RoutingMapping, Routing, SmppConnection
from django.db import transaction


@login_required
def routing_mapping_list(request):
    mappings = RoutingMapping.objects.all().order_by('-created_date')
    routings = Routing.objects.all()
    smpp_list = SmppConnection.objects.all()
    users = User.objects.all()

    return render(request, "routing_mapping_list.html", {
        "mappings": mappings,
        "routings": routings,
        "smpp_list": smpp_list,
        "users": users,
    })


@login_required
@transaction.atomic
def add_routing_mapping(request):
    if request.method == "POST":
        try:
            rout_name = request.POST.get("rout_name")
            routing_id = request.POST.get("routing_id")
            # smpp_id = request.POST.get("smpp_id")
            user_id = request.POST.get("user_id")

            RoutingMapping.objects.create(
                rout_name=rout_name,
                routing_id=routing_id,
                # smpp_id=smpp_id,
                user_id=user_id,
                description=request.POST.get("description"),
                start_date=request.POST.get("start_date"),
                end_date=request.POST.get("end_date"),

                create_by=request.user.username,
                created_date=timezone.now(),
                last_updated_by=request.user.username,
                last_updated_date=timezone.now(),
            )

            messages.success(request, "Routing Mapping added successfully.")
        except Exception as e:
            messages.error(request, f"Error: {e}")

    return redirect("routing_mapping_list")


@login_required
@transaction.atomic
def edit_routing_mapping(request, map_id):
    mapping = get_object_or_404(RoutingMapping, map_id=map_id)

    if request.method == "POST":
        try:
            mapping.rout_name = request.POST.get("rout_name")
            mapping.routing_id = request.POST.get("routing_id")
            # mapping.smpp_id = request.POST.get("smpp_id")
            mapping.user_id = request.POST.get("user_id")

            mapping.description = request.POST.get("description")
            mapping.start_date = request.POST.get("start_date")
            mapping.end_date = request.POST.get("end_date")

            mapping.last_updated_by = request.user.username
            mapping.last_updated_date = timezone.now()
            mapping.save()

            messages.success(request, "Routing Mapping updated successfully.")
        except Exception as e:
            messages.error(request, f"Error: {e}")

    return redirect("routing_mapping_list")


# replace your compose_message view file with this fully corrected version
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.db import close_old_connections, transaction
from .models import (
    User,
    Sender,
    MessageTemplate,
    ComposeMessage,
    ComposeMessageLine,
    SmppConnection,
    RoutingMapping,
)
from sms_app.utils.credit_manager import manage_user_credits
import socket
import struct
import threading
import time
import re
import random
from django.contrib.auth import get_user_model

# ====== Constants ======
ENQUIRE_LINK_INTERVAL = 30
DLR_TIMEOUT = 1200
THREAD_COUNT = 5
MAX_TPS_PER_THREAD = 50

# Map DB template tag -> provider template id (optional)
TEMPLATE_TAG_MAP = {
    # example: 'tag_1401': 'provider_template_id_value'
}

# ====== Utility/debug helpers ======
def dump(label, value):
    try:
        print(f"[DUMP] {label}: ({type(value).__name__}) -> {repr(value)}")
    except Exception as e:
        print(f"[DUMP] {label}: <unprintable> ({e})")

def dump_model_instance(obj, title=None):
    if not obj:
        print(f"[DUMP_MODEL] {title or 'object'}: None")
        return
    try:
        print(f"[DUMP_MODEL] {title or obj.__class__.__name__} START")
        for f in getattr(obj, '_meta').fields:
            name = f.name
            try:
                val = getattr(obj, name)
            except Exception as e:
                val = f"<error reading field: {e}>"
            print(f"  {name}: ({type(val).__name__}) -> {repr(val)}")
        print(f"[DUMP_MODEL] {title or obj.__class__.__name__} END")
    except Exception as e:
        print(f"[DUMP_MODEL] Failed to dump model {title or ''}: {e}")

# ====== Number normalization (Option 1: force 91 prefix) ======
def normalize_to_91(number_raw: str) -> str:
    """Take arbitrary input and return '91XXXXXXXXXX' or raise ValueError."""
    if not number_raw:
        raise ValueError("Empty number")
    s = re.sub(r"[^\d]", "", str(number_raw))
    # Strip leading zeros
    s = re.sub(r"^0+", "", s)
    # If starts with 91 and 12 digits -> keep as-is
    if s.startswith("91") and len(s) == 12:
        return s
    # If 10 digits -> add 91
    if len(s) == 10:
        return "91" + s
    # If starts with +91 then earlier cleaning handled +, so we would have 91... above
    raise ValueError(f"Number '{number_raw}' is not convertible to 91XXXXXXXXXX format")

# ====== SMPP low-level socket ======
class SMPPSocket:
    def __init__(self, host, port, timeout=10):
        self.host = host
        self.port = int(port) if port is not None else None
        self.sock = None
        self.timeout = timeout

    def connect(self):
        print(f"[SOCKET] connect() -> host={self.host} port={self.port}")
        if not self.host:
            raise RuntimeError("[DEBUG SOCKET] Missing host for SMPP connect")
        if not self.port or int(self.port) <= 0:
            raise RuntimeError(f"[DEBUG SOCKET] Invalid port for SMPP connect: {self.port}")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, int(self.port)))
        print("[SOCKET] Connected")

    def send(self, data):
        try:
            print(f"[SOCKET SEND] Sending {len(data)} bytes to {self.host}:{self.port}")
            print(f"[SOCKET HEX] {data[:192].hex()}{'...' if len(data) > 192 else ''}")
            self.sock.sendall(data)
        except Exception as e:
            print(f"[SOCKET] send error: {e}")
            raise

    def receive(self):
        try:
            raw_len = b''
            while len(raw_len) < 4:
                chunk = self.sock.recv(4 - len(raw_len))
                if not chunk:
                    return None
                raw_len += chunk
            pdu_len = struct.unpack('>I', raw_len)[0]
            pdu_rest = b''
            needed = pdu_len - 4
            while len(pdu_rest) < needed:
                chunk = self.sock.recv(needed - len(pdu_rest))
                if not chunk:
                    break
                pdu_rest += chunk
            data = raw_len + pdu_rest
            print(f"[SOCKET RECV] Received PDU {len(data)} bytes")
            print(f"[SOCKET HEX] {data[:192].hex()}{'...' if len(data) > 192 else ''}")
            return data
        except Exception as e:
            print(f"[SOCKET] receive error: {e}")
            return None

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
            print("[SOCKET] Socket closed")

# ====== PDU builders (SMPP field order) ======
def bind_transmitter(system_id, password, system_type, seq):
    body = system_id.encode() + b"\x00" + password.encode() + b"\x00" + system_type.encode() + b"\x00" + struct.pack("BBBB", 0x34, 1, 1, 0)
    cmd_id, cmd_status = 0x00000002, 0
    header = struct.pack(">IIII", 16 + len(body), cmd_id, cmd_status, seq)
    print(f"[PDU] bind_transmitter seq={seq} len={16+len(body)}")
    return header + body

def bind_receiver(system_id, password, system_type, seq):
    body = system_id.encode() + b"\x00" + password.encode() + b"\x00" + system_type.encode() + b"\x00" + struct.pack("BBBB", 0x34, 1, 1, 0)
    cmd_id, cmd_status = 0x00000001, 0
    header = struct.pack(">IIII", 16 + len(body), cmd_id, cmd_status, seq)
    print(f"[PDU] bind_receiver seq={seq} len={16+len(body)}")
    return header + body

def bind_transceiver(system_id, password, system_type, seq):
    body = system_id.encode() + b"\x00" + password.encode() + b"\x00" + system_type.encode() + b"\x00" + struct.pack("BBBB", 0x34, 1, 1, 0)
    cmd_id, cmd_status = 0x00000009, 0
    header = struct.pack(">IIII", 16 + len(body), cmd_id, cmd_status, seq)
    print(f"[PDU] bind_transceiver seq={seq} len={16+len(body)}")
    return header + body

def enquire_link_pdu(seq):
    print(f"[PDU] enquire_link seq={seq}")
    return struct.pack(">IIII", 16, 0x00000015, 0, seq)

# heuristic for GSM7-like content
def looks_like_gsm7(s):
    if not s:
        return True
    for ch in s:
        code = ord(ch)
        if code == 10 or code == 13:
            continue
        if 32 <= code <= 126:
            continue
        return False
    return True

def resolve_template_id(tag):
    if not tag:
        return None
    return TEMPLATE_TAG_MAP.get(tag)

def submit_sm(sender_id,
              destination,
              message,
              seq,
              dest_ton,
              dest_npi,
              src_ton,
              src_npi,
              pe_id=None,
              template_id=None):
    if any(v is None for v in [dest_ton, dest_npi, src_ton, src_npi]):
        raise ValueError("submit_sm requires dest_ton,dest_npi,src_ton,src_npi (no defaults)")

    try:
        src_ton_i = int(src_ton)
        src_npi_i = int(src_npi)
        dest_ton_i = int(dest_ton)
        dest_npi_i = int(dest_npi)
    except Exception as e:
        raise ValueError(f"Invalid TON/NPI values: {e}")

    # pick encoding
    if looks_like_gsm7(message):
        data_coding = 0
        short_message = message.encode('ascii', errors='ignore')
    else:
        data_coding = 8
        short_message = message.encode('utf-16-be')

    sm_length = len(short_message)
    if sm_length > 255:
        raise ValueError("Message is too long (>255 bytes). Implement segmentation/UDH for multipart SMS.")

    service_type = b"\x00"
    esm_class = 0
    protocol_id = 0
    priority_flag = 0
    schedule_delivery_time = b"\x00"
    validity_period = b"\x00"
    registered_delivery = 1
    replace_if_present_flag = 0
    sm_default_msg_id = 0

    # Correct submit_sm field order (SMPP)
    body = (
        service_type +
        struct.pack("B", src_ton_i) +
        struct.pack("B", src_npi_i) +
        sender_id.encode() + b"\x00" +
        struct.pack("B", dest_ton_i) +
        struct.pack("B", dest_npi_i) +
        destination.encode() + b"\x00" +
        struct.pack("BBB", esm_class, protocol_id, priority_flag) +
        schedule_delivery_time + validity_period +
        struct.pack("BB", registered_delivery, replace_if_present_flag) +
        struct.pack("BB", data_coding, sm_default_msg_id) +
        struct.pack("B", sm_length) +
        short_message
    )

    # TLVs: entity id (0x1400), template id (0x1401), sender (0x1402)
    tlvs = b""
    if pe_id:
        pe_bytes = str(pe_id).encode()
        tlvs += struct.pack(">HH", 0x1400, len(pe_bytes)) + pe_bytes
    if template_id:
        t_bytes = str(template_id).encode()
        tlvs += struct.pack(">HH", 0x1401, len(t_bytes)) + t_bytes
    if sender_id:
        s_bytes = sender_id.encode()
        tlvs += struct.pack(">HH", 0x1402, len(s_bytes)) + s_bytes

    cmd_id = 0x00000004
    cmd_status = 0
    total_length = 16 + len(body) + len(tlvs)
    header = struct.pack(">IIII", total_length, cmd_id, cmd_status, seq)
    pdu = header + body + tlvs

    print(f"[PDU] submit_sm seq={seq} len={total_length} dest_ton={dest_ton_i} dest_npi={dest_npi_i} src_ton={src_ton_i} src_npi={src_npi_i} pe_id={pe_id} tpl={template_id} data_coding={data_coding} sm_len={sm_length}")
    print(f"[PDU HEX] {pdu[:192].hex()}{'...' if len(pdu) > 192 else ''}")
    return pdu

# ====== ID helpers ======
def extract_cstring(data_bytes):
    if not data_bytes:
        return ""
    try:
        idx = data_bytes.index(b"\x00")
        raw = data_bytes[:idx]
    except ValueError:
        raw = data_bytes
    try:
        s = raw.decode(errors="ignore")
    except Exception:
        s = "".join(chr(b) for b in raw if 32 <= b <= 126)
    s = re.sub(r"[^A-Za-z0-9\-_:]", "", s)
    return s.strip()

def normalize_msgid_for_store(msgid):
    if not msgid:
        return ""
    m = re.sub(r"[^\w\-:]", "", msgid)
    m = m.strip()
    return m

def normalize_msgid_for_lookup(msgid):
    if not msgid:
        return ""
    m = re.sub(r"[^\w\-:]", "", msgid)
    m = m.lstrip("0")
    return m

# ====== DLR Listener ======
class SMPPDLRListener(threading.Thread):
    def __init__(self, sock, total_lines):
        super().__init__(daemon=True)
        self.sock = sock
        self.total_lines = total_lines
        self.received = 0
        self.seq = 1
        self.lock = threading.Lock()
        self.dlr_pattern = re.compile(
            r"id:(?P<id>[^\s]+).*?stat:(?P<stat>[A-Z]+).*?err:(?P<err>\d+)",
            re.IGNORECASE | re.DOTALL
        )

    def run(self):
        close_old_connections()
        print("[DLR] Listening for DLRs...")
        start_time = time.time()
        last_enquire = time.time()

        while True:
            if 0 < self.total_lines <= self.received and (time.time() - start_time) > 2:
                print("[DLR] Expected DLRs received; exiting listener.")
                break

            if time.time() - start_time > DLR_TIMEOUT:
                print("[DLR] Timeout reached waiting for DLRs.")
                break

            data = self.sock.receive()
            if not data:
                if time.time() - last_enquire > ENQUIRE_LINK_INTERVAL:
                    with self.lock:
                        self.seq += 1
                        try:
                            self.sock.send(enquire_link_pdu(self.seq))
                        except Exception as e:
                            print(f"[DLR] enquire_link send error: {e}")
                    last_enquire = time.time()
                time.sleep(0.1)
                continue

            try:
                total_len, cmd_id, cmd_status, seq = struct.unpack(">IIII", data[:16])
            except Exception:
                print("[DLR] Failed to unpack header")
                continue

            print(f"[DLR] Received PDU cmd_id=0x{cmd_id:08x} cmd_status={cmd_status} seq={seq} len={total_len}")

            if cmd_id == 0x00000005:
                body = data[16:]
                try:
                    sm_text = body.decode(errors="ignore")
                except Exception:
                    sm_text = "".join(chr(b) if 32 <= b <= 126 else " " for b in body)

                print(f"[DLR BODY] {sm_text}")

                match = self.dlr_pattern.search(sm_text)
                msg_id = None
                stat = None
                err = None
                if match:
                    info = match.groupdict()
                    msg_id = info.get("id")
                    stat = info.get("stat", "").upper()
                    err = info.get("err")
                if not msg_id:
                    m = re.search(r"id:([A-Za-z0-9\-_:]+)", sm_text)
                    if m:
                        msg_id = m.group(1)
                if not msg_id:
                    msg_id = "unknown"

                lookup_key = normalize_msgid_for_lookup(msg_id)
                print(f"[DLR] parsed msg_id={msg_id} lookup_key={lookup_key} stat={stat} err={err}")

                try:
                    close_old_connections()
                    line = None
                    if lookup_key and lookup_key != "unknown":
                        line = ComposeMessageLine.objects.filter(message_id=lookup_key).first()
                        if not line:
                            line = ComposeMessageLine.objects.filter(message_id__icontains=lookup_key).first()
                        if not line:
                            line = ComposeMessageLine.objects.filter(message_id__startswith=lookup_key).first()
                    if not line and msg_id != "unknown":
                        line = ComposeMessageLine.objects.filter(message_id__icontains=msg_id).first()

                    if line:
                        dump_model_instance(line, title=f"ComposeMessageLine (found for {msg_id})")
                        if stat:
                            line.status = stat
                            line.masked_status = stat
                        if err:
                            line.masked_reason = err
                            line.reason = err
                        line.dlr_time = timezone.now()
                        if not line.submit_time:
                            line.submit_time = timezone.now()
                        line.save()
                        self.received += 1
                        print(f"[DLR] MsgID={msg_id} | Status={stat or line.status} | DLR time={line.dlr_time} | Receiver={line.mobile_number}")
                    else:
                        print(f"[DLR] MsgID={msg_id} not found in DB (lookup_key={lookup_key})")
                except Exception as e:
                    print(f"[DLR] DB update error: {e}")

                try:
                    resp = struct.pack(">IIII", 16, 0x80000005, 0, seq)
                    self.sock.send(resp)
                except Exception as e:
                    print(f"[DLR] deliver_sm_resp send error: {e}")

            if time.time() - last_enquire > ENQUIRE_LINK_INTERVAL:
                with self.lock:
                    self.seq += 1
                    try:
                        self.sock.send(enquire_link_pdu(self.seq))
                    except Exception as e:
                        print(f"[DLR] periodic enquire error: {e}")
                last_enquire = time.time()

# ====== SMPPSender (uses submit_sm + TLVs) ======
class SMPPSender(threading.Thread):
    def __init__(self, lines, sender_obj, seq_start=1, max_tps=50, smpp_obj=None, entity_id=None, template_id=None):
        super().__init__(daemon=True)
        self.lines = lines
        self.seq = seq_start
        self.max_tps = max_tps
        self.sender_obj = sender_obj
        self.smpp_obj = smpp_obj
        self.entity_id = entity_id
        self.template_id = template_id
        self.sock = None

    def run(self):
        try:
            if not self.smpp_obj:
                raise RuntimeError("SMPPSender requires smpp_obj (DB SmppConnection). Aborting sender thread.")

            host = self.smpp_obj.ip
            port = int(self.smpp_obj.tr_trx_port) if self.smpp_obj.tr_trx_port is not None else None
            system_id = self.smpp_obj.username
            password = self.smpp_obj.password
            system_type = self.smpp_obj.system_type or ""
            dest_ton = self.smpp_obj.dest_addr_ton
            dest_npi = self.smpp_obj.dest_addr_npi
            src_ton = self.smpp_obj.source_addr_ton
            src_npi = self.smpp_obj.source_addr_npi

            dump_model_instance(self.smpp_obj, title="SMPP Config for sender thread")
            dump_model_instance(self.sender_obj, title="Sender Object for sender thread")
            dump("lines_count", len(self.lines))
            for i, ln in enumerate(self.lines):
                print(f"[LINE {i}] id={getattr(ln,'id',None)} mobile={getattr(ln,'mobile_number',None)} text_len={len(getattr(ln,'text_mes',''))}")

            missing = []
            for k, v in [("host", host), ("port", port), ("system_id", system_id), ("password", password),
                         ("dest_ton", dest_ton), ("dest_npi", dest_npi), ("src_ton", src_ton), ("src_npi", src_npi)]:
                if v is None or v == "":
                    missing.append(k)
            if missing:
                raise RuntimeError(f"SMPP configuration missing required fields: {', '.join(missing)}")

            print(f"[SENDER] Connecting to SMPP {host}:{port} as {system_id} (type={system_type})")
            print(f"[SENDER] TON/NPI dest={dest_ton}/{dest_npi} src={src_ton}/{src_npi} DLT pe_id={self.entity_id} tpl={self.template_id} tps={self.max_tps}")

            self.sock = SMPPSocket(host, port)
            self.sock.connect()
            self.sock.send(bind_transmitter(system_id, password, system_type, self.seq))
            self.sock.receive()

            delay = 1 / self.max_tps if self.max_tps and self.max_tps > 0 else 0.02
            for line in self.lines:
                self.seq += 1
                try:
                    sender_name = getattr(self.sender_obj, 'sender_name', '') or ''
                    destination = getattr(line, 'mobile_number', '')
                    message_text = getattr(line, 'text_mes', '') or ''

                    dump("submit_sm.sender_id", sender_name)
                    dump("submit_sm.destination", destination)
                    dump("submit_sm.text_len", len(message_text))
                    dump("submit_sm.seq", self.seq)
                    dump("submit_sm.dest_ton", dest_ton)
                    dump("submit_sm.dest_npi", dest_npi)
                    dump("submit_sm.src_ton", src_ton)
                    dump("submit_sm.src_npi", src_npi)
                    dump("submit_sm.pe_id", self.entity_id)
                    dump("submit_sm.template_id", self.template_id)

                    pdu = submit_sm(
                        sender_id=sender_name,
                        destination=destination,
                        message=message_text,
                        seq=self.seq,
                        dest_ton=dest_ton,
                        dest_npi=dest_npi,
                        src_ton=src_ton,
                        src_npi=src_npi,
                        pe_id=self.entity_id,
                        template_id=self.template_id
                    )
                except Exception as e:
                    print(f"[SENDER] submit_sm build error for {getattr(line,'mobile_number',None)}: {e}")
                    continue

                try:
                    self.sock.send(pdu)
                except Exception as e:
                    print(f"[SENDER] socket send error for {getattr(line,'mobile_number',None)}: {e}")
                    continue

                resp = self.sock.receive()
                if resp:
                    try:
                        _, cmd_id, _, _ = struct.unpack(">IIII", resp[:16])
                        print(f"[SENDER] submit_sm_resp cmd_id=0x{cmd_id:08x}")
                        if cmd_id == 0x80000004:
                            raw = resp[16:]
                            msg_id = extract_cstring(raw)
                            msg_id = normalize_msgid_for_store(msg_id)
                            try:
                                close_old_connections()
                                line.message_id = msg_id
                                line.submit_time = timezone.now()
                                line.sender = sender_name
                                line.receiver = destination
                                line.status = "SUBMITTED"
                                line.save()
                                print(f"[SEND] Receiver={destination} | MsgID={msg_id} | Status=SUBMITTED")
                            except Exception as e:
                                print(f"[SEND] DB save error for {destination}: {e}")
                    except Exception as e:
                        print(f"[ERROR] Parsing submit_sm_resp: {e}")
                else:
                    print(f"[SENDER] No response received for seq={self.seq} dest={destination}")

                time.sleep(delay)
        except Exception as e:
            print(f"[ERROR] Sender thread exception: {e}")
        finally:
            if self.sock:
                self.sock.close()

# ====== compose_message_view (complete) ======
def compose_message_view(request):
    User = get_user_model()
    users = User.objects.all()
    senders = Sender.objects.all()
    templates = MessageTemplate.objects.all()

    if request.method == "POST":
        print("\n======================")
        print("📩 New compose_message POST request received")
        print("======================")

        try:
            sender_id = request.POST.get("senderId")
            send_type = request.POST.get("sendType")
            text_msg = request.POST.get("textMsg") or ""

            dump("request.POST.senderId", sender_id)
            dump("request.POST.sendType", send_type)
            dump("request.POST.textMsg_len", len(text_msg))

            user = request.user if request.user.is_authenticated else None
            if not user:
                messages.error(request, "You must be logged in to send messages.")
                return redirect("compose_message")

            user_pk = getattr(user, "id", None) or getattr(user, "user_id", None)
            if not user_pk:
                messages.error(request, "User ID not found.")
                return redirect("compose_message")

            mobile_numbers_text = request.POST.get("mobileNumbers", "")
            dump("request.POST.mobileNumbers_len", len(mobile_numbers_text))
            raw_numbers = [n.strip() for n in re.split(r"[,\n\r\s]+", mobile_numbers_text) if n.strip()]
            numbers = []
            for rn in raw_numbers:
                try:
                    n = normalize_to_91(rn)
                    numbers.append(n)
                except Exception as e:
                    print(f"[NUMBER] Skipping invalid number {rn}: {e}")
            print(f"📱 Total valid numbers parsed: {len(numbers)} → {numbers}")

            if not numbers:
                messages.error(request, "Please enter at least one valid mobile number in 10-digit or 91-prefixed form.")
                return redirect("compose_message")

            # Credit management
            credit_response = manage_user_credits(
                user=user,
                message_count=len(numbers),
                created_by=user.username
            )
            dump("credit_response", credit_response)
            if not credit_response or credit_response.get("status") == "error":
                messages.error(request, f"Credit check failed: {credit_response.get('message', 'Unknown error')}")
                return redirect("compose_message")

            # Create compose message record
            compose_obj = ComposeMessage.objects.create(
                user_id=user_pk,
                sender_id=sender_id,
                send_type=send_type,
                message_type="TEXT",
                start_date=timezone.now(),
                create_by=user.username,
            )
            dump_model_instance(compose_obj, title="ComposeMessage created")

            # Save lines with placeholders replaced
            lines = []
            placeholder_pattern = re.compile(r"\{#([A-Za-z0-9_]+)#\}")
            for mob in numbers:
                final_text = text_msg
                placeholders = placeholder_pattern.findall(text_msg)
                if placeholders:
                    for ph in placeholders:
                        value = str(random.randint(100000, 999999))
                        final_text = final_text.replace(f"{{#{ph}#}}", value)
                final_text = final_text.replace("\u00a0", " ").strip()
                line = ComposeMessageLine.objects.create(
                    compose_message=compose_obj,
                    mobile_number=mob,
                    text_mes=final_text,
                    create_by=user.username,
                )
                dump_model_instance(line, title=f"Line created for {mob}")
                lines.append(line)

            # Resolve routing mapping -> SMPP
            try:
                route_map = RoutingMapping.objects.select_related("smpp").get(user=user)
                smpp = route_map.smpp
                dump_model_instance(route_map, title="RoutingMapping")
                dump_model_instance(smpp, title="SMPP record")
            except RoutingMapping.DoesNotExist:
                messages.error(request, "No SMPP route is mapped to your account. Contact admin.")
                return redirect("compose_message")
            except Exception as e:
                messages.error(request, "Error determining route for your account.")
                return redirect("compose_message")

            # Validate SMPP fields
            required_fields = {
                "ip": smpp.ip,
                "tr_trx_port": smpp.tr_trx_port,
                "username": smpp.username,
                "password": smpp.password,
                "dest_addr_ton": smpp.dest_addr_ton,
                "dest_addr_npi": smpp.dest_addr_npi,
                "source_addr_ton": smpp.source_addr_ton,
                "source_addr_npi": smpp.source_addr_npi,
            }
            missing = [k for k, v in required_fields.items() if v in (None, "")]
            if missing:
                messages.error(request, f"SMPP configuration incomplete ({', '.join(missing)}). Contact admin.")
                return redirect("compose_message")

            # Prepare dynamic values
            sms_host = smpp.ip
            sms_port = int(smpp.tr_trx_port)
            system_id = smpp.username
            password = smpp.password
            system_type = smpp.system_type or ""
            bind_type = (smpp.bind_type or "transmitter").lower()
            per_smpp_tps = int(smpp.tps) if smpp.tps is not None else MAX_TPS_PER_THREAD
            tx_sessions = int(smpp.tx_sessions) if smpp.tx_sessions is not None else THREAD_COUNT
            rx_port = int(smpp.rx_port) if (smpp.rx_port is not None and int(smpp.rx_port) > 0) else None

            dump("sms_host", sms_host)
            dump("sms_port", sms_port)
            dump("system_id", system_id)
            dump("bind_type", bind_type)
            dump("per_smpp_tps", per_smpp_tps)
            dump("tx_sessions", tx_sessions)
            dump("rx_port (from db)", smpp.rx_port)

            # sender object and entity id (PEID)
            sender_obj = Sender.objects.get(sender_id=sender_id)
            dump_model_instance(sender_obj, title="Sender model")
            entity_id = getattr(sender_obj, 'peid', None) or getattr(sender_obj, 'entity_id', None)
            if not entity_id:
                messages.error(request, "Sender does not have a valid Entity ID (PEID). Contact admin.")
                return redirect("compose_message")
            print(f"🟢 ENTITY ID loaded: {entity_id}")

            # DLT template ID resolution:
            dlt_template_id = None
            # 1) from form
            dlt_from_form = request.POST.get('dltTemplateId')
            if dlt_from_form:
                dlt_template_id = dlt_from_form.strip() or None
            # 2) from template selection (optional)
            if not dlt_template_id:
                template_form_id = request.POST.get('templateId')
                if template_form_id:
                    try:
                        tmpl = MessageTemplate.objects.get(template_id=template_form_id)
                        dlt_template_id = getattr(tmpl, 'dlt_template_id', None) or getattr(tmpl, 'template_identifier', None) or getattr(tmpl, 'templateid', None)
                    except MessageTemplate.DoesNotExist:
                        pass
            # 3) from smpp.dlt_template_id_tag -> map via TEMPLATE_TAG_MAP
            if not dlt_template_id:
                tag = getattr(smpp, 'dlt_template_id_tag', None)
                if tag:
                    resolved = resolve_template_id(tag)
                    if resolved:
                        dlt_template_id = resolved

            if not dlt_template_id:
                messages.error(request, "No DLT template id available. Ensure sender/template/SmppConnection have DLT details.")
                return redirect("compose_message")

            # Bind DLR socket (receiver/transceiver)
            rx_host = sms_host
            rx_port_used = int(rx_port) if rx_port is not None else sms_port
            try:
                rx_sock = SMPPSocket(rx_host, rx_port_used)
                rx_sock.connect()
                if bind_type == "receiver":
                    rx_sock.send(bind_receiver(system_id, password, system_type, 1))
                elif bind_type == "transceiver":
                    rx_sock.send(bind_transceiver(system_id, password, system_type, 1))
                else:
                    rx_sock.send(bind_receiver(system_id, password, system_type, 1))
                rx_sock.receive()
                print("[RX] Bound for DLR (DB-driven)")
            except Exception as e:
                dump_model_instance(smpp, title="SMPP (error context)")
                messages.error(request, f"Failed to bind DLR socket: {e}")
                return redirect("compose_message")

            # Start DLR listener
            dlr_listener = SMPPDLRListener(rx_sock, len(lines))
            dlr_listener.start()
            time.sleep(0.5)

            # Start sender threads
            chunk_size = max(1, len(lines) // tx_sessions)
            for i in range(tx_sessions):
                chunk = lines[i * chunk_size:(i + 1) * chunk_size]
                if not chunk:
                    continue
                seq_start = i * 1000
                t = SMPPSender(
                    chunk,
                    sender_obj,
                    seq_start=seq_start,
                    max_tps=per_smpp_tps,
                    smpp_obj=smpp,
                    entity_id=entity_id,
                    template_id=dlt_template_id
                )
                t.start()

            messages.success(request, f"{len(lines)} messages submitted successfully, DLR listener running.")
            return redirect("compose_message")

        except Exception as e:
            print(f"❌ Error in Compose View: {e}")
            try:
                dump_model_instance(smpp, title="SMPP (on-exception)")
            except Exception:
                pass
            messages.error(request, f"Error sending messages: {e}")

    return render(request, "compose_message.html", {
        "users": users,
        "senders": senders,
        "templates": templates,
    })




# views.py - COMPLETE CORRECTED VERSION
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
import json
import logging
import re
from .models import ComposeMessage, ComposeMessageLine, SmppConnection

logger = logging.getLogger(__name__)

class ComposeMessageView(View):
    def get(self, request):
        """Render the compose message template"""
        smpp_connections = SmppConnection.objects.filter(
            bind_status="BOUND", 
            is_alive=True
        )
        
        context = {
            'smpp_connections': smpp_connections,
        }
        return render(request, 'compose_message1.html', context)

@method_decorator(csrf_exempt, name='dispatch')
class SubmitComposedMessageView(View):
    def post(self, request):
        """Submit composed message to SMPP connection"""
        logger.info("=" * 60)
        logger.info("🚀 COMPOSE MESSAGE SUBMISSION STARTED")
        logger.info("=" * 60)
        
        try:
            # Log raw request
            logger.info(f"📨 Received POST request to /compose-message/submit/")
            
            # Parse JSON data
            try:
                data = json.loads(request.body)
                logger.info(f"✅ JSON parsed successfully: {list(data.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON decode error: {e}")
                return JsonResponse({
                    'status': 'error',
                    'message': f'Invalid JSON data: {str(e)}'
                }, status=400)
            
            # Extract data from request
            smpp_id = data.get('smpp_id')
            sender_id = data.get('sender_id')
            message_text = data.get('message_text')
            mobile_numbers = data.get('mobile_numbers', [])
            template_id = data.get('template_id')
            
            logger.info(f"📋 Extracted parameters:")
            logger.info(f"   SMPP ID: {smpp_id}")
            logger.info(f"   Sender ID: {sender_id}")
            logger.info(f"   Message Length: {len(message_text) if message_text else 0}")
            logger.info(f"   Mobile Numbers: {len(mobile_numbers)}")
            logger.info(f"   Template ID: {template_id}")
            
            # Validate required fields
            if not all([smpp_id, sender_id, message_text, mobile_numbers]):
                error_msg = 'Missing required fields: smpp_id, sender_id, message_text, mobile_numbers'
                logger.error(f"❌ {error_msg}")
                return JsonResponse({
                    'status': 'error',
                    'message': error_msg
                }, status=400)
            
            # Validate SMPP connection
            try:
                smpp_connection = SmppConnection.objects.get(smpp_id=smpp_id)
                logger.info(f"🔌 Found SMPP connection: {smpp_connection.connect_name}")
                logger.info(f"📊 Connection status: {smpp_connection.bind_status}, Alive: {smpp_connection.is_alive}")
                
                if smpp_connection.bind_status != "BOUND" or not smpp_connection.is_alive:
                    error_msg = f'SMPP connection {smpp_connection.connect_name} is not active (Status: {smpp_connection.bind_status}, Alive: {smpp_connection.is_alive})'
                    logger.error(f"❌ {error_msg}")
                    return JsonResponse({
                        'status': 'error',
                        'message': error_msg
                    }, status=400)
                    
            except SmppConnection.DoesNotExist:
                error_msg = f'SMPP connection with id {smpp_id} not found'
                logger.error(f"❌ {error_msg}")
                return JsonResponse({
                    'status': 'error',
                    'message': error_msg
                }, status=404)
            
            # Create ComposeMessage header in transaction
            with transaction.atomic():
                logger.info("💾 Creating ComposeMessage header...")
                compose_message = ComposeMessage.objects.create(
                    send_type='IMMEDIATE',
                    sender_id=sender_id,
                    message_type='SMS',
                    template_id=template_id,
                    create_by=request.user.username if request.user.is_authenticated else 'system'
                )
                logger.info(f"✅ ComposeMessage created with ID: {compose_message.sms_id}")
                
                # Process mobile numbers and send messages
                results = self._send_messages(
                    smpp_connection=smpp_connection,
                    compose_message=compose_message,
                    sender_id=sender_id,
                    message_text=message_text,
                    mobile_numbers=mobile_numbers
                )
            
            logger.info(f"🎯 Message processing completed:")
            logger.info(f"   ✅ Success: {results['success_count']}")
            logger.info(f"   ❌ Failed: {results['failed_count']}")
            logger.info(f"   📊 Total: {results['total_count']}")
            
            return JsonResponse({
                'status': 'success',
                'message': f'Message composed and {results["success_count"]}/{results["total_count"]} messages submitted successfully',
                'compose_message_id': compose_message.sms_id,
                'results': results
            })
            
        except Exception as e:
            logger.error(f"💥 CRITICAL ERROR in SubmitComposedMessageView: {e}", exc_info=True)
            return JsonResponse({
                'status': 'error',
                'message': f'Server error: {str(e)}'
            }, status=500)
    
    def _send_messages(self, smpp_connection, compose_message, sender_id, message_text, mobile_numbers):
        """Send messages to multiple mobile numbers"""
        from .connection_manager import global_connection_manager
        
        results = {
            'total_count': len(mobile_numbers),
            'success_count': 0,
            'failed_count': 0,
            'details': []
        }
        
        logger.info(f"📤 Starting to send {len(mobile_numbers)} messages...")
        
        for i, mobile_number in enumerate(mobile_numbers):
            try:
                logger.info(f"🔹 Processing {i+1}/{len(mobile_numbers)}: {mobile_number}")
                
                # Clean mobile number
                clean_number = self._clean_mobile_number(mobile_number)
                logger.info(f"   📱 Cleaned number: {clean_number}")
                
                # Create ComposeMessageLine record first
                compose_line = ComposeMessageLine.objects.create(
                    compose_message=compose_message,
                    mobile_number=clean_number,
                    text_mes=message_text,
                    sender=sender_id,
                    receiver=clean_number,
                    content=message_text,
                    submit_time=timezone.now(),
                    status='SUBMITTED',
                    create_by=compose_message.create_by,
                    parts=self._calculate_message_parts(message_text)
                )
                logger.info(f"   💾 Created ComposeMessageLine with ID: {compose_line.line_id}")
                
                # Submit message via SMPP
                logger.info(f"   🔄 Submitting to SMPP connection: {smpp_connection.connect_name}")
                message_id = global_connection_manager.submit_message(
                    smpp_id=smpp_connection.smpp_id,
                    source_addr=sender_id,
                    destination_addr=clean_number,
                    message=message_text,
                    custom_params={'line_id': compose_line.line_id}
                )
                logger.info(f"   ✅ SMPP submission successful, Message ID: {message_id}")
                
                # Update line with message ID
                compose_line.message_id = message_id
                compose_line.save()
                logger.info(f"   💾 Updated line with Message ID: {message_id}")
                
                results['success_count'] += 1
                results['details'].append({
                    'mobile_number': clean_number,
                    'status': 'success',
                    'message_id': message_id,
                    'line_id': compose_line.line_id
                })
                
                logger.info(f"   🎉 Successfully processed {clean_number}")
                
            except Exception as e:
                logger.error(f"   ❌ Failed to process {mobile_number}: {str(e)}")
                
                # Update failed record
                if 'compose_line' in locals():
                    compose_line.status = 'FAILED'
                    compose_line.reason = str(e)
                    compose_line.save()
                    logger.info(f"   💾 Marked line {compose_line.line_id} as FAILED")
                
                results['failed_count'] += 1
                results['details'].append({
                    'mobile_number': mobile_number,
                    'status': 'failed',
                    'error': str(e),
                    'line_id': compose_line.line_id if 'compose_line' in locals() else None
                })
        
        logger.info(f"📊 Final results: {results['success_count']} success, {results['failed_count']} failed")
        return results
    
    def _clean_mobile_number(self, mobile_number):
        """Clean and validate mobile number"""
        # Remove any non-digit characters except +
        cleaned = re.sub(r'[^\d+]', '', str(mobile_number).strip())
        
        # Add country code if missing (adjust based on your requirements)
        if not cleaned.startswith('+') and not cleaned.startswith('91'):
            cleaned = '91' + cleaned  # Default to India code
        
        return cleaned
    
    def _calculate_message_parts(self, message_text):
        """Calculate number of message parts"""
        if not message_text:
            return 1
        
        # Simple calculation - 160 chars per part for GSM
        return max(1, (len(message_text) + 159) // 160)

# Add the missing ConnectionStatusView
# views.py - Add this enhanced ConnectionStatusView
class ConnectionStatusView(View):
    def get(self, request, smpp_id):
        """Get connection status for a specific SMPP connection"""
        try:
            print(f"🔍 Checking connection status for SMPP ID: {smpp_id}")
            
            # First check if connection exists in database
            try:
                smpp_connection = SmppConnection.objects.get(smpp_id=smpp_id)
                print(f"📊 Database connection status: {smpp_connection.bind_status}, Alive: {smpp_connection.is_alive}")
            except SmppConnection.DoesNotExist:
                print(f"❌ SMPP connection {smpp_id} not found in database")
                return JsonResponse({
                    'status': 'error',
                    'message': f'SMPP connection {smpp_id} not found in database'
                }, status=404)
            
            # Check connection manager status
            from .connection_manager import global_connection_manager
            manager_status = global_connection_manager.get_connection_status(smpp_id)
            
            if manager_status:
                print(f"✅ Connection manager status: {manager_status}")
                return JsonResponse({
                    'status': 'success',
                    'connection_status': manager_status,
                    'database_status': {
                        'bind_status': smpp_connection.bind_status,
                        'is_alive': smpp_connection.is_alive,
                        'connect_name': smpp_connection.connect_name
                    }
                })
            else:
                print(f"⚠️ Connection not active in connection manager")
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Connection not active in connection manager',
                    'database_status': {
                        'bind_status': smpp_connection.bind_status,
                        'is_alive': smpp_connection.is_alive,
                        'connect_name': smpp_connection.connect_name
                    }
                }, status=404)
                
        except Exception as e:
            print(f"❌ Error getting connection status: {e}")
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
class MessageStatusManager:
    """Manager for handling message status updates (ACK and DLR)"""
    
    @staticmethod
    def update_ack_status(message_id, line_id=None, status='ACK_RECEIVED', ack_message_id=None):
        """Update message line when ACK is received"""
        try:
            print(f"🔄 Starting ACK database update...")
            print(f"📨 Message ID: {message_id}")
            print(f"🔗 Line ID: {line_id}")
            
            if line_id:
                # Update by line_id (preferred)
                message_line = ComposeMessageLine.objects.get(line_id=line_id)
            elif message_id:
                # Update by message_id (fallback)
                message_line = ComposeMessageLine.objects.get(message_id=message_id)
            else:
                print("❌ Cannot update ACK status: neither line_id nor message_id provided")
                return False
            
            message_line.ack_received = True
            message_line.ack_time = timezone.now()
            message_line.ack_message_id = ack_message_id
            message_line.ack_status = status
            message_line.status = status
            message_line.save()
            
            print(f"✅ ACK database updated successfully for line_id: {message_line.line_id}")
            print(f"📊 New Status: {status}")
            logger.info(f"✅ ACK received for Line ID: {message_line.line_id}, Message ID: {message_id}")
            return True
            
        except ComposeMessageLine.DoesNotExist:
            print(f"❌ Message line not found for ACK update: line_id={line_id}, message_id={message_id}")
            logger.error(f"❌ Message line not found for ACK update: line_id={line_id}, message_id={message_id}")
            return False
        except Exception as e:
            print(f"❌ Error updating ACK status: {e}")
            logger.error(f"❌ Error updating ACK status: {e}")
            return False
    
    @staticmethod
    def update_dlr_status(message_id, dlr_data):
        """Update message line when DLR is received"""
        try:
            print(f"🔄 Starting DLR database update...")
            print(f"📊 Updating database for Message ID: {message_id}")
            
            message_line = ComposeMessageLine.objects.get(message_id=message_id)
            
            # Parse DLR data
            status = dlr_data.get('status', 'UNKNOWN')
            dlr_error_code = dlr_data.get('error_code', '000')
            dlr_text = dlr_data.get('dlr_text', '')
            
            # Map DLR status to our status
            status_mapping = {
                'DELIVRD': 'DELIVERED',
                'DELIVERED': 'DELIVERED',
                'UNDELIV': 'UNDELIVERED',
                'UNDELIVERED': 'UNDELIVERED',
                'REJECTD': 'REJECTED',
                'REJECTED': 'REJECTED',
                'EXPIRED': 'EXPIRED',
                'ACCEPTD': 'ACK_RECEIVED',
                'UNKNOWN': 'UNKNOWN'
            }
            
            final_status = status_mapping.get(status, 'UNKNOWN')
            
            # Update message line
            message_line.dlr_received = True
            message_line.dlr_time = timezone.now()
            message_line.status = status
            message_line.dlr_error_code = dlr_error_code
            message_line.status = final_status
            message_line.reason = dlr_text
            
            print(f"📈 New Status: {final_status} (DLR Status: {status})")
            print(f"❌ Error Code: {dlr_error_code}")
            print(f"✅ Database updated successfully for line_id: {message_line.line_id}")
            
            message_line.save()
            
            logger.info(f"📨 DLR processed for Message ID: {message_id}, Status: {final_status}")
            return True
            
        except ComposeMessageLine.DoesNotExist:
            print(f"❌ Message line not found for DLR update: message_id={message_id}")
            logger.error(f"❌ Message line not found for DLR update: message_id={message_id}")
            return False
        except Exception as e:
            print(f"❌ Error updating DLR status: {e}")
            logger.error(f"❌ Error updating DLR status: {e}")
            return False

@method_decorator(csrf_exempt, name='dispatch')
class GetLineDetailsView(View):
    def get(self, request, line_id):
        """Get detailed information for a specific message line"""
        try:
            message_line = get_object_or_404(ComposeMessageLine, line_id=line_id)
            
            line_data = {
                'line_id': message_line.line_id,
                'mobile_number': message_line.mobile_number,
                'message_id': message_line.message_id,
                'status': message_line.status,
                'reason': message_line.reason,
                'submit_time': message_line.submit_time.isoformat() if message_line.submit_time else None,
                'dlr_time': message_line.dlr_time.isoformat() if message_line.dlr_time else None,
                'ack_received': message_line.ack_received,
                'ack_time': message_line.ack_time.isoformat() if message_line.ack_time else None,
                'dlr_received': message_line.dlr_received,
                'dlr_status': message_line.dlr_status,
                'dlr_error_code': message_line.dlr_error_code,
                'parts': message_line.parts,
                'encoding': message_line.encoding,
                'attributes_1': message_line.attributes_1,
                'attributes_2': message_line.attributes_2,
                'attributes_3': message_line.attributes_3,
                'attributes_4': message_line.attributes_4,
                'attributes_5': message_line.attributes_5,
                'attributes_6': message_line.attributes_6,
                'attributes_7': message_line.attributes_7,
            }
            
            return JsonResponse({
                'status': 'success',
                'line': line_data
            })
                
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)

class ComposeMessageStatusView(View):
    """View to check status of a compose message"""
    def get(self, request, compose_message_id):
        try:
            compose_message = get_object_or_404(ComposeMessage, sms_id=compose_message_id)
            lines = compose_message.lines.all()
            
            status_summary = {
                'total': lines.count(),
                'submitted': lines.filter(status='SUBMITTED').count(),
                'ack_received': lines.filter(status='ACK_RECEIVED').count(),
                'delivered': lines.filter(status='DELIVERED').count(),
                'undelivered': lines.filter(status='UNDELIVERED').count(),
                'failed': lines.filter(status='FAILED').count(),
            }
            
            lines_data = []
            for line in lines:
                lines_data.append({
                    'line_id': line.line_id,
                    'mobile_number': line.mobile_number,
                    'message_id': line.message_id,
                    'status': line.status,
                    'submit_time': line.submit_time.isoformat() if line.submit_time else None,
                    'dlr_time': line.dlr_time.isoformat() if line.dlr_time else None,
                    'ack_received': line.ack_received,
                    'dlr_received': line.dlr_received,
                })
            
            return JsonResponse({
                'status': 'success',
                'compose_message': {
                    'sms_id': compose_message.sms_id,
                    'sender_id': compose_message.sender_id,
                    'created_date': compose_message.created_date.isoformat(),
                },
                'summary': status_summary,
                'lines': lines_data
            })
                
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
        







# ===============================connection control views=================================
from django.http import JsonResponse
from django.views.generic import ListView, DetailView
from .models import SmppConnection
from .smpp_client import FixedSMPPClient
import threading
import time
import json  # Add this import

class SmppConnectionListView(ListView):
    model = SmppConnection
    template_name = 'list.html'
    context_object_name = 'connections'
    
    def get_queryset(self):
        return SmppConnection.objects.all().order_by('-last_updated_date')

class SmppConnectionDetailView(DetailView):
    model = SmppConnection
    template_name = 'smpp_connections.html'
    context_object_name = 'connection'
    pk_url_kwarg = 'smpp_id'

# class BindConnectionView(View):
#     def post(self, request, smpp_id):
#         smpp_connection = get_object_or_404(SmppConnection, smpp_id=smpp_id)
        
#         try:
#             client = FixedSMPPClient(smpp_connection)
            
#             # Update status to retrying
#             smpp_connection.bind_status = "RETRYING"
#             smpp_connection.save()
            
#             # Connect and bind
#             if client.bind_connection():
#                 # Start background monitoring
#                 self._start_connection_monitoring(smpp_connection)
#                 return JsonResponse({
#                     'status': 'success',
#                     'message': 'Bind successful'
#                 })
#             else:
#                 smpp_connection.bind_status = "FAILED"
#                 smpp_connection.save()
#                 return JsonResponse({
#                     'status': 'error',
#                     'message': smpp_connection.last_error or 'Bind failed'
#                 })
                
#         except Exception as e:
#             smpp_connection.bind_status = "FAILED"
#             smpp_connection.last_error = str(e)
#             smpp_connection.save()
#             return JsonResponse({
#                 'status': 'error',
#                 'message': f'Bind failed: {str(e)}'
#             })

#     def _start_connection_monitoring(self, smpp_connection):
#         """Start background monitoring for connection health"""
#         def monitor():
#             while True:
#                 try:
#                     # Check if connection should still be monitored
#                     current_conn = SmppConnection.objects.get(smpp_id=smpp_connection.smpp_id)
#                     if current_conn.bind_status != "BOUND":
#                         break
                        
#                     # Send enquire link
#                     client = FixedSMPPClient(current_conn)
#                     client.enquire_link()
                    
#                     # Update uptime
#                     current_conn.uptime_seconds += 30
#                     current_conn.save()
                    
#                     # Wait for next check
#                     time.sleep(30)
                    
#                 except SmppConnection.DoesNotExist:
#                     break
#                 except Exception:
#                     break
        
#         thread = threading.Thread(target=monitor)
#         thread.daemon = True
#         thread.start()
import time

start_time = time.time()  # current time in seconds since epoch

class BindConnectionView(View):
    def post(self, request, smpp_id):
        print(f"=== STARTING BIND PROCESS FOR SMPP CONNECTION ID: {smpp_id} ===")
        print("TIMESTAMP START:", time.time())

        # Step 1: Fetch SMPP connection
        smpp_connection = get_object_or_404(SmppConnection, smpp_id=smpp_id)

        print(f"Found SMPP connection: ID={smpp_connection.smpp_id}, Name={smpp_connection.connect_name}")
        print("TIMESTAMP AFTER DB:", time.time())

        try:
            # Step 2: QUICK CHECK - Don't use SessionManager.get() which hangs
            print("\nStep 2: Performing quick session check...")
            print("TIMESTAMP BEFORE QUICK CHECK:", time.time())
            
            # Check if we have a session in memory (without network checks)
            quick_session = None
            with threading.Lock():  # Simple lock for thread safety
                quick_session = SMPPSessionManager._sessions.get(smpp_id)
            
            if quick_session:
                print(f"Found existing session object in memory")
                print(f"Session attributes: connected={quick_session.connected}, bound={quick_session.bound}")
                
                # Just check attributes, no network calls
                if quick_session.connected and quick_session.bound:
                    print("✅ Session already exists and appears connected/bound")
                    return JsonResponse({
                        'status': 'success',
                        'message': 'Session already exists',
                        'session_exists': True,
                        'details': {
                            'name': smpp_connection.connect_name,
                            'connected': True,
                            'bound': True
                        }
                    })
            
            print("TIMESTAMP AFTER QUICK CHECK:", time.time())
            
            # Step 3: Create NEW session directly (bypass SessionManager to avoid its checks)
            print("\nStep 3: Creating new SMPP client directly...")
            client = FixedSMPPClient(smpp_connection)
            print("TIMESTAMP AFTER CLIENT CREATION:", time.time())
            
            # Step 4: Update status to RETRYING
            print("\nStep 4: Updating database status...")
            smpp_connection.bind_status = "RETRYING"
            smpp_connection.save()
            print("TIMESTAMP AFTER DB UPDATE:", time.time())
            
            # Step 5: Attempt bind with timeout
            print("\nStep 5: Attempting bind...")
            print(f"Host: {smpp_connection.ip}:{smpp_connection.tr_trx_port}")
            print(f"Username: {smpp_connection.username}")
            print("TIMESTAMP BEFORE BIND:", time.time())
            
            # Set socket timeout for connect
            import socket
            
            # Try connect with timeout
            connect_start = time.time()
            CONNECT_TIMEOUT = 10  # seconds
            
            if not client.connect():
                print(f"❌ Connect failed within {time.time() - connect_start:.1f}s")
                return JsonResponse({
                    'status': 'error',
                    'message': 'TCP connection failed'
                })
            
            print(f"✅ TCP connected in {time.time() - connect_start:.1f}s")
            print("TIMESTAMP AFTER CONNECT:", time.time())
            
            # Try bind with timeout
            bind_start = time.time()
            BIND_TIMEOUT = 30  # seconds
            
            if not client.bind_connection():
                print(f"❌ Bind failed within {time.time() - bind_start:.1f}s")
                client.force_close()
                return JsonResponse({
                    'status': 'error', 
                    'message': smpp_connection.last_error or 'Bind failed'
                })
            
            print(f"✅ SMPP bind successful in {time.time() - bind_start:.1f}s")
            print("TIMESTAMP AFTER BIND:", time.time())
            
            # Step 6: Store session in SessionManager (without its health checks)
            print("\nStep 6: Storing session in SessionManager...")
            with threading.Lock():
                SMPPSessionManager._sessions[smpp_id] = client
                SMPPSessionManager._session_info[smpp_id] = {
                    'created_at': time.time(),
                    'connection_name': smpp_connection.connect_name,
                    'bind_type': smpp_connection.bind_type,
                    'username': smpp_connection.username
                }
            
            print("TIMESTAMP AFTER STORING:", time.time())
            
            # Step 7: Update database
            print("\nStep 7: Updating database with success...")
            smpp_connection.bind_status = "BOUND"
            smpp_connection.last_error = None
            smpp_connection.last_bind_time = timezone.now()
            smpp_connection.is_alive = True
            smpp_connection.save()
            
            print("TIMESTAMP AFTER FINAL DB UPDATE:", time.time())
            
            # Step 8: Get session stats
            total_sessions = len(SMPPSessionManager._sessions)
            print(f"\n📊 SESSION STATISTICS:")
            print(f"   Total sessions in memory: {total_sessions}")
            
            # Step 9: Return success
            total_time = time.time() - start_time
            print(f"\n✅ BIND COMPLETE IN {total_time:.1f} SECONDS")
            
            return JsonResponse({
                'status': 'success',
                'message': 'Bind successful',
                'time_seconds': round(total_time, 1),
                'details': {
                    'name': smpp_connection.connect_name,
                    'username': smpp_connection.username,
                    'host': smpp_connection.ip,
                    'port': smpp_connection.tr_trx_port,
                    'bind_type': smpp_connection.bind_type
                }
            })

        except Exception as e:
            print(f"\n❌ EXCEPTION: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            print(f"TIMESTAMP ON ERROR: {time.time()}")

            smpp_connection.bind_status = "FAILED"
            smpp_connection.last_error = str(e)
            smpp_connection.is_alive = False
            smpp_connection.save()

            return JsonResponse({
                'status': 'error',
                'message': f'Bind failed: {str(e)}'
            })

class SendSMSView(View):
    def post(self, request, smpp_id):
        smpp_connection = get_object_or_404(SmppConnection, smpp_id=smpp_id)
        
        try:
            # Get data from request
            data = json.loads(request.body)
            destination = data.get('destination')
            message = data.get('message')
            
            client = FixedSMPPClient(smpp_connection)
            
            # Send SMS
            result = client.submit_sm(destination, message)
            
            if result and result.get('status') == 0:
                return JsonResponse({
                    'status': 'success',
                    'message': 'SMS sent successfully',
                    'message_id': result.get('message_id')
                })
            else:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Failed to send SMS'
                })
                
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': f'SMS send failed: {str(e)}'
            })

class UnbindConnectionView(View):
    def post(self, request, smpp_id):
        smpp_connection = get_object_or_404(SmppConnection, smpp_id=smpp_id)
        
        try:
            client = FixedSMPPClient(smpp_connection)
            client.unbind()
            
            # Update connection status
            smpp_connection.bind_status = "UNBOUND"
            smpp_connection.save()
            
            return JsonResponse({
                'status': 'success',
                'message': 'Connection unbound successfully'
            })
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': f'Failed to unbind: {str(e)}'
            })

class EnquireLinkView(View):
    def post(self, request, smpp_id):
        smpp_connection = get_object_or_404(SmppConnection, smpp_id=smpp_id)
        
        try:
            client = FixedSMPPClient(smpp_connection)
            success = client.enquire_link()
            
            return JsonResponse({
                'status': 'success' if success else 'error',
                'message': 'Enquire link sent' if success else 'Enquire link failed',
                'is_alive': success
            })
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': f'Enquire link failed: {str(e)}'
            })
        
# ===============================End smpp connection control views=================================
import json
import logging
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, render
from .models import SmppConnection, ComposeMessage, ComposeMessageLine
from .smpp_client import FixedSMPPClient
from .dlr_listener import DLRListener


@method_decorator(csrf_exempt, name='dispatch')
class GetDLRReportView(View):
    def get(self, request, message_id):
        """Get DLR report for specific message ID"""
        global dlr_reports
        
        if message_id in dlr_reports:
            return JsonResponse({
                'status': 'success',
                'message': 'DLR report found',
                'dlr_report': dlr_reports[message_id]
            })
        else:
            return JsonResponse({
                'status': 'error',
                'message': 'No DLR report found for this message ID'
            }, status=404)

@method_decorator(csrf_exempt, name='dispatch')
class GetAllDLRReportsView(View):
    def get(self, request):
        """Get all DLR reports"""
        global dlr_reports
        
        return JsonResponse({
            'status': 'success',
            'total_reports': len(dlr_reports),
            'dlr_reports': dlr_reports
        })
@method_decorator(csrf_exempt, name='dispatch')
class StopDLRListenerView(View):
    def get(self, request):
        """Stop DLR listener"""
        global dlr_listener
        if dlr_listener:
            dlr_listener.stop_listening()
            dlr_listener = None
            return JsonResponse({'status': 'success', 'message': 'DLR listener stopped'})
        else:
            return JsonResponse({'status': 'error', 'message': 'No DLR listener running'})

@method_decorator(csrf_exempt, name='dispatch')
class DLRReportView(View):
    def get(self, request):
        """Get all DLR reports"""
        global dlr_listener
        
        if not dlr_listener:
            return JsonResponse({
                'status': 'error', 
                'message': 'DLR listener not running'
            })
        
        reports = dlr_listener.get_dlr_reports()
        csv_report = dlr_listener.generate_csv_report()
        
        return JsonResponse({
            'status': 'success',
            'total_reports': len(reports),
            'csv_report': csv_report,
            'reports': reports
        })

@method_decorator(csrf_exempt, name='dispatch')
class LatestDLRView(View):
    def get(self, request):
        """Get latest DLR report"""
        global dlr_listener
        
        if not dlr_listener:
            return JsonResponse({
                'status': 'error', 
                'message': 'DLR listener not running'
            })
        
        latest_report = dlr_listener.get_latest_dlr_report()
        
        if latest_report:
            detailed_report = dlr_listener.report_parser.generate_detailed_report(latest_report)
            return JsonResponse({
                'status': 'success',
                'report': latest_report,
                'detailed_report': detailed_report
            })
        else:
            return JsonResponse({
                'status': 'error', 
                'message': 'No DLR reports available'
            })

@method_decorator(csrf_exempt, name='dispatch')
class ClearDLRReportsView(View):
    def post(self, request):
        """Clear all DLR reports"""
        global dlr_listener
        
        if not dlr_listener:
            return JsonResponse({
                'status': 'error', 
                'message': 'DLR listener not running'
            })
        
        dlr_listener.clear_dlr_reports()
        
        return JsonResponse({
            'status': 'success',
            'message': 'All DLR reports cleared'
        })

@method_decorator(csrf_exempt, name='dispatch')
class SMPPStatusView(View):
    def get(self, request):
        """Get current SMPP connection status"""
        global smpp_client, dlr_listener
        
        status_info = {
            'smpp_client_connected': smpp_client.connected if smpp_client else False,
            'smpp_client_bound': smpp_client.bound if smpp_client else False,
            'dlr_listener_running': dlr_listener.listening if dlr_listener else False,
            'dlr_reports_count': dlr_listener.report_parser.get_report_count() if dlr_listener else 0
        }
        
        return JsonResponse({
            'status': 'success',
            'connection_status': status_info
        })

@method_decorator(csrf_exempt, name='dispatch')
class SendTestSMSView(View):
    def post(self, request):
        """Send test SMS with custom parameters"""
        global smpp_client, dlr_listener
        
        try:
            data = json.loads(request.body)
            
            # Default to hardcoded values if not provided
            smpp_id = data.get('smpp_id', 3)
            destination = data.get('destination', '9181020701215')
            message = data.get('message', 'The OTP for your Change password Request is 123456 VD STRIDE MEDIA')
            otp = data.get('otp', '123456')
            sender_id = data.get('sender_id', 'VDSLON')
            
            print(f"\n TEST SMS REQUEST")
            print(f"   • Destination: {destination}")
            print(f"   • OTP: {otp}")
            print(f"   • Sender: {sender_id}")
            
            # Get SMPP connection
            smpp_connection = get_object_or_404(SmppConnection, smpp_id=smpp_id)
            
            # Create new client
            smpp_client = FixedSMPPClient(smpp_connection)
            
            # Connect and bind
            if not smpp_client.connect() or not smpp_client.bind_connection():
                return JsonResponse({
                    'status': 'error',
                    'message': 'SMPP connection failed'
                }, status=400)
            
            # Start DLR listener
            if not dlr_listener:
                dlr_listener = DLRListener(smpp_client)
                dlr_listener.start_listening()
            
            # Replace OTP if template contains {#var#}
            if '{#var#}' in message:
                final_message = message.replace('{#var#}', otp)
            else:
                final_message = message
            
            # Send SMS
            result = smpp_client.submit_sm_with_dlt(
                destination=destination,
                message=final_message,
                sender_id=sender_id,
                template_id='1707172898474018613',
                pe_id='1501404380000052845',
                dlt_telemarketer_id='1207161893755246802'
            )
            
            if result and result.get('status') == 0:
                return JsonResponse({
                    'status': 'success',
                    'message': 'Test SMS sent successfully',
                    'data': {
                        'message_id': result.get('message_id'),
                        'destination': destination,
                        'sender_id': sender_id,
                        'otp_used': otp,
                        'sent_timestamp': str(timezone.now())
                    }
                })
            else:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Test SMS failed'
                }, status=500)
                
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': f'Test SMS failed: {str(e)}'
            }, status=500)
from django.shortcuts import render
from django.http import JsonResponse
from .models import (
    Sender, MessageTemplate, SmppConnection, RoutingMapping, RoutingLine,
    ComposeMessage, ComposeMessageLine
)
from .smpp_manager import SMPPSessionManager
from .tlv_utils import tagname_to_int
import re

# -----------------------------
# Utility to extract message_id
# -----------------------------
def extract_message_id(submit_resp: dict) -> str:
    """
    Safely extract message_id from submit_resp or raw_response_body.
    Handles bytes, raw PDU, and non-alphanumeric characters.
    """
    # Try direct message_id
    message_id = submit_resp.get("message_id")
    if message_id:
        if isinstance(message_id, bytes):
            message_id = message_id.decode(errors="ignore")
        return message_id.strip()

    # Try raw_response_body or raw_response_body_hex
    raw_body = submit_resp.get("raw_response_body") or submit_resp.get("raw_response_body_hex")
    if raw_body:
        if isinstance(raw_body, bytes):
            # SMPP often returns C-Octet string ending with null
            null_pos = raw_body.find(b"\x00")
            if null_pos > 0:
                raw_body = raw_body[:null_pos]
            raw_body = raw_body.decode(errors="ignore")
        # Try to extract with regex first
        match = re.search(r"id[:=]([a-f0-9\-]+)", raw_body, re.IGNORECASE)
        if match:
            return match.group(1)
        # Fallback: remove unwanted chars
        cleaned = "".join(c for c in raw_body if c.isalnum() or c == "-")
        return cleaned[:50] if cleaned else "N/A"

    return "N/A"

from django.shortcuts import render

from django.db import transaction
import re
import logging

# Import your models and the new WalletService
from .models import (
    Sender, Credit, ComposeMessage, ComposeMessageLine,
    RoutingMapping, SmppConnection, RoutingLine, Wallet, User
)
from .wallet_services import WalletService
from .smpp_manager import SMPPSessionManager # adjust import if different

log = logging.getLogger(__name__)
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _submit_sms_batch_in_background(payload):
    """Submit a compose batch after the HTTP response has returned."""
    close_old_connections()
    try:
        user = User.objects.get(user_id=payload["user_id"])
        wallet = Wallet.objects.get(wallet_id=payload["wallet_id"])
        compose_msg = ComposeMessage.objects.get(sms_id=payload["compose_message_id"])
        chosen_smpp = SmppConnection.objects.get(smpp_id=payload["smpp_id"])

        session = SMPPSessionManager.get(chosen_smpp.smpp_id)
        if not session:
            session = SMPPSessionManager.start_session(chosen_smpp.smpp_id)
        if not session:
            logger.error("Unable to create SMPP session for async batch %s", payload["batch_request_id"])
            return

        from .smpp_dlr_manager import DLRManager
        DLRManager.start_listener(session)

        line_buffer = []
        success_count = 0
        failed_count = 0
        batch_size = int(payload.get("db_batch_size") or 1000)

        for idx, mob in enumerate(payload["mobile_numbers"], 1):
            tlvs = dict(payload.get("extra_tlvs") or {})

            if chosen_smpp.dlt_enable:
                if payload.get("peid") and chosen_smpp.dlt_entity_id_tag:
                    tlvs[tagname_to_int(chosen_smpp.dlt_entity_id_tag)] = payload["peid"]
                if payload.get("template_id") and chosen_smpp.send_dlt_template_id and chosen_smpp.dlt_template_id_tag:
                    tlvs[tagname_to_int(chosen_smpp.dlt_template_id_tag)] = payload["template_id"]
                if payload.get("tmid"):
                    tlvs[0x1402] = payload["tmid"]

            try:
                result = session.sms_sender.submit_sm_with_tlvs(
                    source_addr=payload["sender_name"],
                    destination_addr=mob,
                    message=payload["message"],
                    smpp_config=payload["smpp_config"],
                    tlvs_map=tlvs
                )
                if not result:
                    raise Exception("No response from SMPP server")

                command_status = result.get("command_status", 0)
                if command_status != 0:
                    raise Exception(result.get("command_status_text", f"SMPP Error: {command_status}"))

                message_id = result.get("message_id")
                sequence_number = result.get("sequence_number")
                success_count += 1
                line_buffer.append(ComposeMessageLine(
                    compose_message=compose_msg,
                    mobile_number=mob,
                    text_mes=payload["message"],
                    sender=payload["sender_name"],
                    receiver=mob,
                    content=payload["message"],
                    message_id=message_id,
                    sequence_number=sequence_number,
                    submit_time=timezone.now(),
                    status=result.get("command_status_text", "SUBMITTED"),
                    encoding=payload["text_type"],
                    content_id=payload["template_id"],
                    tmid=payload["tmid"] or "",
                    parts=payload["parts_per_message"],
                    attributes_1=message_id or "",
                    attributes_2=payload["batch_request_id"],
                    attributes_3=f"SMSC: {chosen_smpp.connect_name}",
                    attributes_4=f"Encoding: {payload['text_type']}",
                    attributes_5=json.dumps({
                        "submitted": True,
                        "wallet_deduction_type": wallet.deduction_type,
                        "smpp_id": chosen_smpp.smpp_id,
                        "tlv_count": len(tlvs),
                        "ton_used": payload["source_ton"],
                        "npi_used": payload["source_npi"],
                        "dlr_requested": payload["smpp_config"].get("registered_delivery", 0) == 1,
                        "message_id": message_id,
                        "command_status": command_status,
                    }),
                    create_by=payload["username"]
                ))
            except Exception as exc:
                failed_count += 1
                line_buffer.append(ComposeMessageLine(
                    compose_message=compose_msg,
                    mobile_number=mob,
                    text_mes=payload["message"],
                    sender=payload["sender_name"],
                    receiver=mob,
                    content=payload["message"],
                    status="FAILED",
                    reason=str(exc)[:2000],
                    submit_time=timezone.now(),
                    parts=payload["parts_per_message"],
                    attributes_1="",
                    attributes_2=payload["batch_request_id"],
                    attributes_3=f"SMSC: {chosen_smpp.connect_name}",
                    attributes_4=f"TMID Attempted: {(payload['tmid'] or '')[:50]}...",
                    attributes_5=json.dumps({
                        "error": str(exc)[:500],
                        "tlv_count": len(tlvs),
                        "ton_attempted": payload["source_ton"],
                        "npi_attempted": payload["source_npi"],
                    }),
                    create_by=payload["username"]
                ))
                logger.error("Async submit failed for %s via %s: %s", mob, chosen_smpp.connect_name, exc)

            if len(line_buffer) >= batch_size:
                ComposeMessageLine.objects.bulk_create(line_buffer)
                line_buffer.clear()

            if idx % 1000 == 0:
                logger.info(
                    "Async batch %s progress: %s/%s submitted, success=%s failed=%s",
                    payload["batch_request_id"], idx, len(payload["mobile_numbers"]), success_count, failed_count
                )

        if line_buffer:
            ComposeMessageLine.objects.bulk_create(line_buffer)

        if wallet.deduction_type == "SUBMISSION" and failed_count > 0:
            refund_parts = failed_count * payload["parts_per_message"]
            if refund_parts > 0:
                WalletService.create_refund_entry(
                    user=user,
                    wallet=wallet,
                    request_id=payload["batch_request_id"],
                    refund_parts=refund_parts,
                    reason=f"Refund for {failed_count} failed messages",
                    comments=f"Auto-refund for failed submissions in batch {payload['batch_request_id']}"
                )

        logger.info(
            "Async batch %s complete: success=%s failed=%s total=%s",
            payload["batch_request_id"], success_count, failed_count, len(payload["mobile_numbers"])
        )
    except Exception:
        logger.exception("Async SMS batch crashed: %s", payload.get("batch_request_id"))
    finally:
        close_old_connections()


from django.shortcuts import render
from django.db import transaction
import re
import json
import logging

from sms_app.models import (
    Sender,
    ComposeMessage,
    ComposeMessageLine,
    RoutingMapping,
    RoutingLine,
    SmppConnection,
    Credit,
    Wallet
)


# log = logging.getLogger("submit_sms")

# @login_required
# def send_sms(request):
#     user = request.user

#     # All available senders
#     senders = Sender.objects.filter(user=user, active_flag=True)

#     # Credit fetch or create
#     user_credit = Credit.objects.filter(user=user).order_by('-credit_id').first()
#     if not user_credit:
#         user_credit = Credit.objects.create(
#             user=user,
#             action_type='Credit',
#             used_credit=0,
#             ending_balance=0,
#             starting_balance=0,
#             comments='Initial credit record'
#         )

#     # Recent messages
#     recent_messages = ComposeMessageLine.objects.filter(
#         compose_message__user_id=user.user_id
#     ).order_by('-submit_time')[:10]

#     # Load routing
#     mapping = RoutingMapping.objects.filter(user=user).first()
#     routing = mapping.routing if mapping else None
#     connections = SmppConnection.objects.filter(bind_status="BOUND", is_alive=True) if routing else SmppConnection.objects.none()

#     if request.method == "POST":
#         # DEBUG: Log form data
#         logger.info(f"📤 FORM DATA RECEIVED: {dict(request.POST)}")
#         logger.info(f"📤 SMPP ID FROM FORM: {request.POST.get('smpp_id')}")
        
#         sender_id = request.POST.get("sender_name")
#         sender_obj = Sender.objects.filter(sender_id=sender_id, user=user).first()
#         if not sender_obj:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "Invalid sender selected",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         actual_sender_name = sender_obj.sender_name

#         # Form fields
#         mobile_numbers_raw = request.POST.get("mobile_numbers")
#         template_id = request.POST.get("template_id")
#         peid = request.POST.get("peid")
#         tmid = request.POST.get("tmid")
#         message = request.POST.get("message", "")
#         send_type = request.POST.get("send_type", "batch")
#         template_identifier = request.POST.get("template_identifier", "")
#         text_type = request.POST.get("text_type", "english")
#         schedule = request.POST.get("schedule", "immediate")
#         smart_link = request.POST.get("smart_link", "disable")
#         smart_link_url = request.POST.get("smart_link_url", "")
#         remove_duplicates = request.POST.get("remove_duplicates", "enable")
#         flash_message = request.POST.get("flash_message", "disable")
#         whatsapp_url = request.POST.get("whatsapp_url", "")

#         # Basic validation
#         if not sender_id or not message or not mobile_numbers_raw:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "Sender, Numbers, and Message are required",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         # Parse numbers
#         mobile_numbers = re.split(r"[\s,]+", mobile_numbers_raw.strip())
#         mobile_numbers = [m for m in mobile_numbers if m]
#         if remove_duplicates == "enable":
#             mobile_numbers = list(dict.fromkeys(mobile_numbers))

#         if not mobile_numbers:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "No valid mobile numbers",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         # Credit calculation
#         chars = 160 if text_type == "english" else 70
#         parts_per_message = (len(message) + chars - 1) // chars if len(message) > 0 else 1
#         total_parts_needed = len(mobile_numbers) * parts_per_message

#         # Get user wallet
#         wallet = WalletService.get_user_wallet(user)
#         if not wallet:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "No wallet found for user. Please contact support.",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         # Prepare messages data
#         messages_data = [{"parts": parts_per_message, "reference_id": mob, "mobile_number": mob} for mob in mobile_numbers]

#         # Handle deduction based on wallet type
#         if wallet.deduction_type == "SUBMISSION":
#             deduct_res = WalletService.deduct_immediate(
#                 user=user,
#                 wallet=wallet,
#                 parts=total_parts_needed,
#                 reference_id=None,  # WalletService generates batch_id
#                 comments=f"Batch immediate deduction for {len(mobile_numbers)} messages"
#             )
#             if not deduct_res.get("ok"):
#                 return render(request, "submit_sm.html", {
#                     "connections": connections,
#                     "senders": senders,
#                     "error": deduct_res.get("message", "Unable to deduct credits"),
#                     "user_credit": user_credit,
#                     "recent_messages": recent_messages
#                 })
#             batch_request_id = deduct_res["credit"].reference_id  # ✅ Get batch_id

#         else:  # DELIVERED
#             reserve = WalletService.create_pending_reservation(
#                 user=user,
#                 wallet=wallet,
#                 total_parts=total_parts_needed,
#                 message_count=len(mobile_numbers),
#                 request_id=None,  # WalletService generates batch_id
#                 reference_ids=mobile_numbers,
#                 comments=f"Batch reservation for {len(mobile_numbers)} messages (DELIVERED deduction)"
#             )
#             if not reserve.get("ok"):
#                 return render(request, "submit_sm.html", {
#                     "connections": connections,
#                     "senders": senders,
#                     "error": reserve.get("message", "Unable to reserve credits"),
#                     "user_credit": user_credit,
#                     "recent_messages": recent_messages
#                 })
#             batch_request_id = reserve["pending"].reference_id  # ✅ Get batch_id

#         # Collect TLVs
#         extra_tlvs = {}
#         for key in request.POST:
#             if key.startswith("tlv_tag_"):
#                 idx = key.split("_")[-1]
#                 tag_val = request.POST.get(key)
#                 val_val = request.POST.get(f"tlv_value_{idx}")
#                 if tag_val and val_val:
#                     try:
#                         extra_tlvs[tagname_to_int(tag_val)] = val_val
#                     except Exception:
#                         pass

#         # ============================================================================
#         # FIXED: GET SMPP CONNECTION (USER SELECTION FIRST, THEN ROUTING FALLBACK)
#         # ============================================================================
#         smpp_id_from_form = request.POST.get("smpp_id")
#         chosen_smpp = None
        
#         if smpp_id_from_form:
#             try:
#                 # Try to use the SMPP connection selected by user in the form
#                 chosen_smpp = SmppConnection.objects.get(
#                     smpp_id=smpp_id_from_form,
#                     bind_status="BOUND",
#                     is_alive=True
#                 )
#                 logger.info(f"✅ Using user-selected SMPP: {chosen_smpp.connect_name} (ID: {chosen_smpp.smpp_id})")
#             except SmppConnection.DoesNotExist:
#                 logger.warning(f"⚠️ User selected SMPP ID {smpp_id_from_form} not available, falling back to routing")
#                 chosen_smpp = None
        
#         # If no user selection or not available, use routing
#         if not chosen_smpp and routing:
#             lines = RoutingLine.objects.filter(
#                 rout=routing,
#                 smpp__bind_status="BOUND",
#                 smpp__is_alive=True
#             ).select_related("smpp")

#             if lines.exists():
#                 chosen_smpp = lines.first().smpp
#                 logger.info(f"🔄 Falling back to routing SMPP: {chosen_smpp.connect_name} (ID: {chosen_smpp.smpp_id})")
#             else:
#                 logger.error("❌ No active SMPP lines found in routing")
#         elif not chosen_smpp and not routing:
#             logger.error("❌ No routing configured and no SMPP selected")
        
#         if not chosen_smpp:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "No active SMPP connection available",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         logger.info(f"🎯 FINAL SELECTED SMPP: {chosen_smpp.connect_name} (ID: {chosen_smpp.smpp_id})")
        
#         # Check for existing session in SessionManager
#         logger.info(f"🔍 Checking for existing session for smpp_id: {chosen_smpp.smpp_id}")
#         session = SMPPSessionManager.get(chosen_smpp.smpp_id)
        
#         if not session:
#             logger.info(f"🆕 No existing session found, starting new session for SMPP ID: {chosen_smpp.smpp_id}")
#             session = SMPPSessionManager.start_session(chosen_smpp.smpp_id)
        
#         if not session:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": f"Unable to create SMPP session for {chosen_smpp.connect_name}",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         logger.info(f"✅ Session obtained: {session} for SMPP: {chosen_smpp.connect_name}")
#         # ============================================================================

#         if not getattr(session, "dlr_listener_started", False):
#             from .smpp_dlr_manager import DLRListener
#             listener = DLRListener(session)
#             listener.start()
#             session.dlr_listener_started = True
#         from .smpp_dlr_manager import DLRManager

#         # Start DLR listener for this session
#         listener = DLRManager.start_listener(session)
#         if listener:
#             logger.info(f"✅ DLR listener started for SMPP: {chosen_smpp.connect_name}")
#         else:
#             logger.warning(f"⚠️ Could not start DLR listener for SMPP: {chosen_smpp.connect_name}")

#         # SMPP config
#         smpp_config = {
#             "source_addr_ton": chosen_smpp.source_addr_ton or 5,
#             "source_addr_npi": chosen_smpp.source_addr_npi or 0,
#             "dest_addr_ton": chosen_smpp.dest_addr_ton or 1,
#             "dest_addr_npi": chosen_smpp.dest_addr_npi or 1,
#             "registered_delivery": 1,
#             "pe_id": peid,
#             "template_id": template_id,
#         }

#         if flash_message == "enable":
#             smpp_config["esm_class"] = 16

#         # SAVE MAIN COMPOSE MESSAGE
#         with transaction.atomic():
#             compose_msg = ComposeMessage.objects.create(
#                 user_id=user.user_id,
#                 send_type=send_type,
#                 sender_id=sender_id,
#                 message_type="text",
#                 template_id=template_id,
#                 template_list=template_identifier,
#                 smart_link_flag=(smart_link == "enable"),
#                 remove_duplications_flag=(remove_duplicates == "enable"),
#                 send_as_flag_messsage_flag=(flash_message == "enable"),
#                 schedule=schedule,
#                 whatsapp_url=whatsapp_url,
#                 start_date=timezone.now(),
#                 end_date=timezone.now() if schedule == "immediate" else None,
#                 attributes_1=smart_link_url if smart_link == "enable" else "",
#                 attributes_2=text_type,
#                 attributes_3=f"Parts/message: {parts_per_message}",
#                 attributes_4=f"Length: {len(message)}",
#                 attributes_5=f"SenderName: {actual_sender_name}",
#                 attributes_6=batch_request_id,
#                 attributes_7=json.dumps({
#                     "total_parts": total_parts_needed,
#                     "message_count": len(mobile_numbers),
#                     "deduction_type": wallet.deduction_type,
#                     "plan_type": wallet.plan_type,
#                     "smpp_used": chosen_smpp.connect_name,
#                     "smpp_id": chosen_smpp.smpp_id
#                 }),
#                 create_by=user.username,
#                 last_updated_by=user.username
#             )

#         # SEND SMS ONE-BY-ONE
#         line_objs = []
#         success_count = 0
#         failed_count = 0

#         for mob in mobile_numbers:
#             tlvs = extra_tlvs.copy()
#             if chosen_smpp.dlt_enable:
#                 if peid and chosen_smpp.dlt_entity_id_tag:
#                     tlvs[tagname_to_int(chosen_smpp.dlt_entity_id_tag)] = peid
#                 if template_id and chosen_smpp.send_dlt_template_id and chosen_smpp.dlt_template_id_tag:
#                     tlvs[tagname_to_int(chosen_smpp.dlt_template_id_tag)] = template_id
#                 if tmid:
#                     tlvs[0x1402] = tmid
#             try:
#                 result = session.sms_sender.submit_sm_with_tlvs(
#                     source_addr=actual_sender_name,
#                     destination_addr=mob,
#                     message=message,
#                     smpp_config=smpp_config,
#                     tlvs_map=tlvs
#                 )
#                 message_id = result.get("message_id")
#                 sequence_number = result.get("sequence_number")
#                 success_count += 1

#                 line_objs.append(ComposeMessageLine(
#                     compose_message=compose_msg,
#                     mobile_number=mob,
#                     text_mes=message,
#                     sender=actual_sender_name,
#                     receiver=mob,
#                     content=message,
#                     message_id=message_id,
#                     sequence_number=sequence_number,
#                     submit_time=timezone.now(),
#                     status=result.get("command_status_text", "SUBMITTED"),
#                     encoding=text_type,
#                     content_id=template_id,
#                     tmid=tmid,
#                     parts=parts_per_message,
#                     attributes_1=message_id or "",
#                     attributes_2=batch_request_id,
#                     attributes_3=f"SMSC: {chosen_smpp.connect_name}",
#                     attributes_4=f"Encoding: {text_type}",
#                     attributes_5=f"SmartLink: {smart_link}",
#                     attributes_6=json.dumps({
#                         "submitted": True,
#                         "wallet_deduction_type": wallet.deduction_type,
#                         "smpp_id": chosen_smpp.smpp_id
#                     }),
#                     create_by=user.username
#                 ))
                
#                 logger.info(f"📨 Sent to {mob} via {chosen_smpp.connect_name}, Message ID: {message_id}")
#             except Exception as e:
#                 failed_count += 1
#                 line_objs.append(ComposeMessageLine(
#                     compose_message=compose_msg,
#                     mobile_number=mob,
#                     text_mes=message,
#                     sender=actual_sender_name,
#                     receiver=mob,
#                     content=message,
#                     status="FAILED",
#                     reason=str(e)[:2000],
#                     submit_time=timezone.now(),
#                     parts=parts_per_message,
#                     attributes_1="",
#                     attributes_2=batch_request_id,
#                     attributes_3=f"SMSC: {chosen_smpp.connect_name}",
#                     create_by=user.username
#                 ))
#                 logger.error(f"❌ Failed to submit to {mob} via {chosen_smpp.connect_name}: {e}")

#         if line_objs:
#             ComposeMessageLine.objects.bulk_create(line_objs)

#         # Refund failed messages if SUBMISSION
#         if wallet.deduction_type == "SUBMISSION" and failed_count > 0:
#             refund_parts = failed_count * parts_per_message
#             if refund_parts > 0:
#                 WalletService.create_refund_entry(
#                     user=user,
#                     wallet=wallet,
#                     request_id=batch_request_id,
#                     refund_parts=refund_parts,
#                     reason=f"Refund for {failed_count} failed messages",
#                     comments=f"Auto-refund for failed submissions in batch {batch_request_id}"
#                 )

#         # Refresh user credit
#         user_credit = Credit.objects.filter(user=user).order_by('-credit_id').first()
#         recent_messages = ComposeMessageLine.objects.filter(
#             compose_message__user_id=user.user_id
#         ).order_by('-submit_time')[:10]

#         # Return to UI
#         return render(request, "submit_sm.html", {
#             "connections": connections,
#             "senders": senders,
#             "result": f"✔ Successfully submitted {success_count} messages (failed: {failed_count}) using Sender: {actual_sender_name} via {chosen_smpp.connect_name}",
#             "message_id": batch_request_id,
#             "status_text": f"Submitted {success_count}/{len(mobile_numbers)} via {chosen_smpp.connect_name}",
#             "seq_number": batch_request_id,
#             "user_credit": user_credit,
#             "recent_messages": recent_messages
#         })

#     # GET request
#     return render(request, "submit_sm.html", {
#         "connections": connections,
#         "senders": senders,
#         "user_credit": user_credit,
#         "recent_messages": recent_messages
#     })

log = logging.getLogger("submit_sms")
@login_required
def send_sms(request):
    user = request.user

    # All available senders
    senders = get_accessible_senders_queryset(user, active_only=True)
    wallet = WalletService.get_user_wallet(user)

    # Credit fetch or create
    user_credit = Credit.objects.filter(user=user).order_by('-credit_id').first()
    if not user_credit:
        user_credit = Credit.objects.create(
            user=user,
            action_type='Credit',
            used_credit=0,
            ending_balance=0,
            starting_balance=0,
            comments='Initial credit record'
        )

    # Recent messages
    recent_messages = ComposeMessageLine.objects.filter(
        compose_message__user_id=user.user_id
    ).order_by('-submit_time')[:10]

    # Load routing
    mapping = RoutingMapping.objects.filter(user=user).first()
    routing = mapping.routing if mapping else None
    connections = SmppConnection.objects.filter(bind_status="BOUND", is_alive=True) if routing else SmppConnection.objects.none()

    def render_submit_page(**extra_context):
        hash_entry = HashTable.objects.filter(user=user).first()
        groups = GroupHeader.objects.filter(user=user).order_by('group_name')
        context = {
            "connections": connections,
            "senders": senders,
            "user_credit": user_credit,
            "recent_messages": recent_messages,
            "hash_entry": hash_entry,
            "wallet_balance": wallet.balance if wallet else Decimal("0"),
            "wallet_plan_type": wallet.plan_type if wallet else "",
            "wallet_deduction_type": wallet.deduction_type if wallet else "",
            "groups": groups,
        }
        context.update(extra_context)
        return render(request, "submit_sm.html", context)

    if request.method == "POST":
        # DEBUG: Log form data
        logger.info(f"📤 FORM DATA RECEIVED: {dict(request.POST)}")
        logger.info(f"📤 SMPP ID FROM FORM: {request.POST.get('smpp_id')}")
        
        sender_id = request.POST.get("sender_name")
        sender_obj = get_accessible_senders_queryset(user, active_only=True).filter(sender_id=sender_id).first()
        if not sender_obj:
            return render_submit_page(error="Invalid sender selected")

        actual_sender_name = sender_obj.sender_name

        # Form fields
        mobile_numbers_raw = request.POST.get("mobile_numbers")
        template_id = request.POST.get("template_id")
        peid = request.POST.get("peid")
        
        # Get TMID from HashTable ONLY
        hash_entry = HashTable.objects.filter(user=user).first()
        tmid = ""
        if hash_entry and hash_entry.hash_value:
            # Use hash value as TMID
            tmid = str(hash_entry.hash_value)
            logger.info(f"📝 Using TMID from hash table: {tmid}...")
        else:
            logger.warning("⚠️ No hash entry or hash value found for user")
            return render_submit_page(error="No hash configuration found. Please configure hash settings first.")
        
        message = request.POST.get("message", "")
        send_type = request.POST.get("send_type", "batch")
        template_identifier = request.POST.get("template_identifier", "")
        text_type = request.POST.get("text_type", "english")
        schedule = request.POST.get("schedule", "immediate")
        smart_link = request.POST.get("smart_link", "disable")
        smart_link_url = request.POST.get("smart_link_url", "")
        remove_duplicates = request.POST.get("remove_duplicates", "enable")
        flash_message = request.POST.get("flash_message", "disable")
        whatsapp_url = request.POST.get("whatsapp_url", "")

        # Basic validation
        if not sender_id or not message or not mobile_numbers_raw:
            return render_submit_page(error="Sender, Numbers, and Message are required")

        # Parse numbers
        mobile_numbers = re.split(r"[\s,]+", mobile_numbers_raw.strip())
        mobile_numbers = [m for m in mobile_numbers if m]
        if remove_duplicates == "enable":
            mobile_numbers = list(dict.fromkeys(mobile_numbers))

        if not mobile_numbers:
            return render_submit_page(error="No valid mobile numbers")

        # Credit calculation
        chars = 160 if text_type == "english" else 70
        parts_per_message = (len(message) + chars - 1) // chars if len(message) > 0 else 1
        total_parts_needed = len(mobile_numbers) * parts_per_message

        # Get user wallet
        wallet = WalletService.get_user_wallet(user)
        if not wallet:
            return render_submit_page(error="No wallet found for user. Please contact support.")

        if wallet.plan_type == "Prepaid" and Decimal(wallet.balance or 0) < total_parts_needed:
            return render_submit_page(
                error=(
                    f"Insufficient balance! Available: ₹{wallet.balance}, "
                    f"Required: ₹{total_parts_needed} for {len(mobile_numbers)} message(s)."
                )
            )

        # Prepare messages data
        messages_data = [{"parts": parts_per_message, "reference_id": mob, "mobile_number": mob} for mob in mobile_numbers]

        # Handle deduction based on wallet type
        if wallet.deduction_type == "SUBMISSION":
            deduct_res = WalletService.deduct_immediate(
                user=user,
                wallet=wallet,
                parts=total_parts_needed,
                reference_id=None,
                comments=f"Batch immediate deduction for {len(mobile_numbers)} messages"
            )
            if not deduct_res.get("ok"):
                return render_submit_page(error=deduct_res.get("message", "Unable to deduct credits"))
            batch_request_id = deduct_res["credit"].reference_id
        else:  # DELIVERED
            reserve = WalletService.create_pending_reservation(
                user=user,
                wallet=wallet,
                total_parts=total_parts_needed,
                message_count=len(mobile_numbers),
                request_id=None,
                reference_ids=mobile_numbers,
                comments=f"Batch reservation for {len(mobile_numbers)} messages (DELIVERED deduction)"
            )
            if not reserve.get("ok"):
                return render_submit_page(error=reserve.get("message", "Unable to reserve credits"))
            batch_request_id = reserve["pending"].reference_id

        # Collect TLVs
        extra_tlvs = {}
        for key in request.POST:
            if key.startswith("tlv_tag_"):
                idx = key.split("_")[-1]
                tag_val = request.POST.get(key)
                val_val = request.POST.get(f"tlv_value_{idx}")
                if tag_val and val_val:
                    try:
                        if tagname_to_int(tag_val) != 0x1402:
                            extra_tlvs[tagname_to_int(tag_val)] = val_val
                    except Exception:
                        pass

        # ============================================================================
        # GET SMPP CONNECTION
        # ============================================================================
        smpp_id_from_form = request.POST.get("smpp_id")
        chosen_smpp = None
        
        if smpp_id_from_form:
            try:
                chosen_smpp = SmppConnection.objects.get(
                    smpp_id=smpp_id_from_form,
                    bind_status="BOUND",
                    is_alive=True
                )
                logger.info(f"✅ Using user-selected SMPP: {chosen_smpp.connect_name} (ID: {chosen_smpp.smpp_id})")
            except SmppConnection.DoesNotExist:
                logger.warning(f"⚠️ User selected SMPP ID {smpp_id_from_form} not available")
                chosen_smpp = None
        
        if not chosen_smpp and routing:
            lines = RoutingLine.objects.filter(
                rout=routing,
                smpp__bind_status="BOUND",
                smpp__is_alive=True
            ).select_related("smpp")

            if lines.exists():
                chosen_smpp = lines.first().smpp
                logger.info(f"🔄 Falling back to routing SMPP: {chosen_smpp.connect_name} (ID: {chosen_smpp.smpp_id})")
            else:
                logger.error("❌ No active SMPP lines found in routing")
        
        if not chosen_smpp:
            return render_submit_page(error="No active SMPP connection available")

        logger.info(f"🎯 FINAL SELECTED SMPP: {chosen_smpp.connect_name} (ID: {chosen_smpp.smpp_id})")
        
        # CRITICAL: DEBUG SMSC CONFIGURATION
        print(f"\n🔍 SMPP CONFIGURATION CHECK:")
        print(f"   Name: {chosen_smpp.connect_name}")
        print(f"   Source TON (DB): {chosen_smpp.source_addr_ton}")
        print(f"   Source NPI (DB): {chosen_smpp.source_addr_npi}")
        print(f"   Sender Name: '{actual_sender_name}'")
        print(f"   Is Alphanumeric: {actual_sender_name.isalpha()}")
        
        # Check if session exists
        existing_session = SMPPSessionManager.get(chosen_smpp.smpp_id)
        
        if existing_session:
            # Check if session is still connected
            try:
                if hasattr(existing_session, 'is_really_connected') and existing_session.is_really_connected():
                    logger.info(f"✅ Reusing existing session for SMPP ID: {chosen_smpp.smpp_id}")
                    session = existing_session
                else:
                    logger.warning(f"⚠️ Existing session is dead, creating new one")
                    session = SMPPSessionManager.start_session(chosen_smpp.smpp_id)
            except Exception as e:
                logger.error(f"❌ Error checking session: {e}")
                session = SMPPSessionManager.start_session(chosen_smpp.smpp_id)
        else:
            logger.info(f"🆕 Creating new session for SMPP ID: {chosen_smpp.smpp_id}")
            session = SMPPSessionManager.start_session(chosen_smpp.smpp_id)
        
        if not session:
            return render_submit_page(error=f"Unable to create SMPP session for {chosen_smpp.connect_name}")

        # Start DLR listener
        from .smpp_dlr_manager import DLRManager
        dlr_listener = DLRManager.start_listener(session)
        
        if dlr_listener:
            logger.info(f"✅ DLR listener started for SMPP: {chosen_smpp.connect_name}")

        # ============================================================================
        # FIXED: SMPP CONFIGURATION - FORCE TON=5 FOR ALPHANUMERIC SENDER
        # ============================================================================
        # Determine correct TON/NPI based on sender type
        if actual_sender_name.isalpha():
            # Alphanumeric sender MUST use TON=5, NPI=0
            source_ton = 5
            source_npi = 0
            print(f"   ✅ ALPHANUMERIC SENDER: Using TON=5, NPI=0")
        else:
            # Numeric sender
            source_ton = chosen_smpp.source_addr_ton or 1
            source_npi = chosen_smpp.source_addr_npi or 1
            print(f"   ✅ NUMERIC SENDER: Using TON={source_ton}, NPI={source_npi}")

        # SMPP config with CORRECT TON/NPI
        smpp_config = {
            "source_addr_ton": source_ton,  # CRITICAL FIX: 5 for alphanumeric
            "source_addr_npi": source_npi,  # CRITICAL FIX: 0 for alphanumeric
            "dest_addr_ton": chosen_smpp.dest_addr_ton or 1,
            "dest_addr_npi": chosen_smpp.dest_addr_npi or 1,
            "registered_delivery": 1,
            "pe_id": peid,
            "template_id": template_id,
            "service_type": "OTP",  # Add service_type for OTP messages
        }

        if flash_message == "enable":
            smpp_config["esm_class"] = 16

        print(f"\n⚙️ FINAL SMPP CONFIGURATION:")
        for key, value in smpp_config.items():
            print(f"   {key}: {value}")

        # SAVE MAIN COMPOSE MESSAGE
        with transaction.atomic():
            compose_msg = ComposeMessage.objects.create(
                user_id=user.user_id,
                send_type=send_type,
                sender_id=sender_id,
                message_type="text",
                template_id=template_id,
                template_list=template_identifier,
                smart_link_flag=(smart_link == "enable"),
                remove_duplications_flag=(remove_duplicates == "enable"),
                send_as_flag_messsage_flag=(flash_message == "enable"),
                schedule=schedule,
                whatsapp_url=whatsapp_url,
                start_date=timezone.now(),
                end_date=timezone.now() if schedule == "immediate" else None,
                attributes_1=smart_link_url if smart_link == "enable" else "",
                attributes_2=text_type,
                attributes_3=f"Parts/message: {parts_per_message}",
                attributes_4=f"Length: {len(message)}",
                attributes_5=f"SenderName: {actual_sender_name}",
                attributes_6=batch_request_id,
                attributes_7=json.dumps({
                    "total_parts": total_parts_needed,
                    "message_count": len(mobile_numbers),
                    "deduction_type": wallet.deduction_type,
                    "plan_type": wallet.plan_type,
                    "smpp_used": chosen_smpp.connect_name,
                    "smpp_id": chosen_smpp.smpp_id,
                    "tmid_source": "hash_table",
                    "tmid_value": tmid,
                    "ton_used": source_ton,  # Log which TON we're using
                    "npi_used": source_npi,  # Log which NPI we're using
                    "is_alphanumeric": actual_sender_name.isalpha(),
                    "hash_type": hash_entry.type if hash_entry else "NONE"
                }),
                create_by=user.username,
                last_updated_by=user.username
            )

        worker_payload = {
            "user_id": user.user_id,
            "username": user.username,
            "wallet_id": wallet.wallet_id,
            "compose_message_id": compose_msg.sms_id,
            "smpp_id": chosen_smpp.smpp_id,
            "mobile_numbers": mobile_numbers,
            "message": message,
            "sender_name": actual_sender_name,
            "template_id": template_id,
            "peid": peid,
            "tmid": tmid,
            "text_type": text_type,
            "parts_per_message": parts_per_message,
            "batch_request_id": batch_request_id,
            "extra_tlvs": extra_tlvs,
            "smpp_config": smpp_config,
            "source_ton": source_ton,
            "source_npi": source_npi,
        }
        threading.Thread(
            target=_submit_sms_batch_in_background,
            args=(worker_payload,),
            daemon=True,
            name=f"SMSSubmit-{batch_request_id}"
        ).start()

        user_credit = Credit.objects.filter(user=user).order_by('-credit_id').first()
        recent_messages = ComposeMessageLine.objects.filter(
            compose_message__user_id=user.user_id
        ).order_by('-submit_time')[:10]

        return render(request, "submit_sm.html", {
            "connections": connections,
            "senders": senders,
            "result": (
                f"✔ Batch accepted. Background submission started for {len(mobile_numbers)} "
                f"message(s) using Sender: {actual_sender_name} via {chosen_smpp.connect_name}"
            ),
            "message_id": batch_request_id,
            "status_text": f"Queued {len(mobile_numbers)} messages for background submit via {chosen_smpp.connect_name}",
            "seq_number": batch_request_id,
            "user_credit": user_credit,
            "recent_messages": recent_messages,
            "tmid_used": tmid if tmid else "",
            "ton_used": source_ton,
            "npi_used": source_npi,
            "sender_type": "Alphanumeric" if actual_sender_name.isalpha() else "Numeric"
        })

        # SEND SMS WITH PROPER TLV HANDLING
        line_objs = []
        success_count = 0
        failed_count = 0

        print(f"\n📤 SENDING MESSAGES:")
        print(f"   Total Numbers: {len(mobile_numbers)}")
        print(f"   Message: '{message[:50]}...'")
        print(f"   Template ID: {template_id}")
        print(f"   PEID: {peid}")
        print(f"   Sender: '{actual_sender_name}' (TON={source_ton}, NPI={source_npi})")

        for idx, mob in enumerate(mobile_numbers, 1):
            # Create TLVs map
            tlvs = extra_tlvs.copy()
            
            print(f"\n   ──────────────────────────────────────")
            print(f"   📱 MOBILE {idx}/{len(mobile_numbers)}: {mob}")
            
            # Add DLT TLVs if enabled
            if chosen_smpp.dlt_enable:
                print(f"   📊 DLT ENABLED - ADDING TLVs:")
                
                # Add PEID
                if peid and chosen_smpp.dlt_entity_id_tag:
                    peid_tag = tagname_to_int(chosen_smpp.dlt_entity_id_tag)
                    tlvs[peid_tag] = peid
                    print(f"      ✅ TLV: {chosen_smpp.dlt_entity_id_tag} (0x{peid_tag:04X})")
                    print(f"           Value: {peid}")
                    print(f"           Length: {len(peid)} chars")
                
                # Add Template ID
                if template_id and chosen_smpp.send_dlt_template_id and chosen_smpp.dlt_template_id_tag:
                    template_tag = tagname_to_int(chosen_smpp.dlt_template_id_tag)
                    tlvs[template_tag] = template_id
                    print(f"      ✅ TLV: {chosen_smpp.dlt_template_id_tag} (0x{template_tag:04X})")
                    print(f"           Value: {template_id}")
                    print(f"           Length: {len(template_id)} chars")
                
                # Add TMID (0x1402)
                if tmid:
                    # Shorten TMID to 20 chars max
                    safe_tmid = str(tmid)
                    print(f"      🔐 TLV: TMID (0x1402)")
                    print(f"           Value: {safe_tmid}")
                    print(f"           Length: {len(safe_tmid)} characters")
                    
                    tlvs[0x1402] = safe_tmid
                
                print(f"   📦 TOTAL TLVs: {len(tlvs)}")
            
            try:
                print(f"   📤 Sending attempt...")
                
                result = session.sms_sender.submit_sm_with_tlvs(
                    source_addr=actual_sender_name,
                    destination_addr=mob,
                    message=message,
                    smpp_config=smpp_config,
                    tlvs_map=tlvs
                )
                
                if not result:
                    raise Exception("No response from SMPP server")
                
                message_id = result.get("message_id")
                sequence_number = result.get("sequence_number")
                command_status = result.get("command_status", 0)
                
                # Check for ERROR 083 specifically
                if "083" in str(result.get("error", "")) or "0x00000083" in str(result.get("error", "")):
                    print(f"   ❌ ERROR 083 DETECTED: Invalid Source TON")
                    print(f"       Your sender '{actual_sender_name}' needs TON=5, NPI=0")
                    raise Exception(f"ERROR 083: Invalid Source TON. Fix SMSC configuration.")
                
                if command_status != 0:
                    error_msg = result.get("command_status_text", f"SMPP Error: {command_status}")
                    raise Exception(error_msg)
                
                # Success!
                success_count += 1
                
                print(f"   ✅ SUCCESS!")
                print(f"       Message ID: {message_id}")
                print(f"       Sequence: {sequence_number}")
                print(f"       Status: {result.get('command_status_text', 'SUBMITTED')}")
                print(f"       Command Status: {command_status}")
                
                line_objs.append(ComposeMessageLine(
                    compose_message=compose_msg,
                    mobile_number=mob,
                    text_mes=message,
                    sender=actual_sender_name,
                    receiver=mob,
                    content=message,
                    message_id=message_id,
                    sequence_number=sequence_number,
                    submit_time=timezone.now(),
                    status=result.get("command_status_text", "SUBMITTED"),
                    encoding=text_type,
                    content_id=template_id,
                    tmid=tmid if tmid else "",
                    parts=parts_per_message,
                    attributes_1=message_id or "",
                    attributes_2=batch_request_id,
                    attributes_3=f"SMSC: {chosen_smpp.connect_name}",
                    attributes_4=f"Encoding: {text_type}",
                    attributes_5=json.dumps({
                        "submitted": True,
                        "wallet_deduction_type": wallet.deduction_type,
                        "smpp_id": chosen_smpp.smpp_id,
                        "tmid_sent": True,
                        "tmid_length": len(tmid),
                        "tlv_count": len(tlvs),
                        "ton_used": source_ton,
                        "npi_used": source_npi,
                        "dlr_requested": smpp_config.get("registered_delivery", 0) == 1,
                        "message_id": message_id,
                        "command_status": command_status,
                        "command_status_text": result.get("command_status_text", "")
                    }),
                    create_by=user.username
                ))
                
                logger.info(f"📨 Sent to {mob} via {chosen_smpp.connect_name}, Message ID: {message_id}")
                    
            except Exception as e:
                failed_count += 1
                print(f"   ❌ FAILED: {str(e)}")
                
                line_objs.append(ComposeMessageLine(
                    compose_message=compose_msg,
                    mobile_number=mob,
                    text_mes=message,
                    sender=actual_sender_name,
                    receiver=mob,
                    content=message,
                    status="FAILED",
                    reason=str(e)[:2000],
                    submit_time=timezone.now(),
                    parts=parts_per_message,
                    attributes_1="",
                    attributes_2=batch_request_id,
                    attributes_3=f"SMSC: {chosen_smpp.connect_name}",
                    attributes_4=f"TMID Attempted: {tmid[:50]}...",
                    attributes_5=json.dumps({
                        "error": str(e)[:500],
                        "tlv_count": len(tlvs),
                        "tmid_length": len(tmid),
                        "tmid_attempted": tmid,
                        "ton_attempted": source_ton,
                        "npi_attempted": source_npi,
                        "is_alphanumeric": actual_sender_name.isalpha()
                    }),
                    create_by=user.username
                ))
                logger.error(f"❌ Failed to submit to {mob} via {chosen_smpp.connect_name}: {e}")

        if line_objs:
            ComposeMessageLine.objects.bulk_create(line_objs)

        # Refund failed messages if SUBMISSION
        if wallet.deduction_type == "SUBMISSION" and failed_count > 0:
            refund_parts = failed_count * parts_per_message
            if refund_parts > 0:
                WalletService.create_refund_entry(
                    user=user,
                    wallet=wallet,
                    request_id=batch_request_id,
                    refund_parts=refund_parts,
                    reason=f"Refund for {failed_count} failed messages",
                    comments=f"Auto-refund for failed submissions in batch {batch_request_id}"
                )

        print(f"\n" + "="*60)
        print(f"📊 FINAL RESULTS:")
        print(f"   Success: {success_count}")
        print(f"   Failed: {failed_count}")
        print(f"   Total: {len(mobile_numbers)}")
        print(f"   TON Used: {source_ton}")
        print(f"   NPI Used: {source_npi}")
        print(f"   Sender Type: {'Alphanumeric' if actual_sender_name.isalpha() else 'Numeric'}")
        print(f"\n" + "="*60)

        # Refresh user credit
        user_credit = Credit.objects.filter(user=user).order_by('-credit_id').first()
        recent_messages = ComposeMessageLine.objects.filter(
            compose_message__user_id=user.user_id
        ).order_by('-submit_time')[:10]

        # Return to UI
        return render(request, "submit_sm.html", {
            "connections": connections,
            "senders": senders,
            "result": f"✔ Successfully submitted {success_count} messages (failed: {failed_count}) using Sender: {actual_sender_name} via {chosen_smpp.connect_name}",
            "message_id": batch_request_id,
            "status_text": f"Submitted {success_count}/{len(mobile_numbers)} via {chosen_smpp.connect_name}",
            "seq_number": batch_request_id,
            "user_credit": user_credit,
            "recent_messages": recent_messages,
            "tmid_used": tmid if tmid else "",
            "ton_used": source_ton,
            "npi_used": source_npi,
            "sender_type": "Alphanumeric" if actual_sender_name.isalpha() else "Numeric"
        })

    # GET request
    return render_submit_page()
# @login_required
# def send_sms(request):
#     user = request.user

#     # All available senders
#     senders = Sender.objects.filter(user=user, active_flag=True)

#     # Credit fetch or create
#     user_credit = Credit.objects.filter(user=user).order_by('-credit_id').first()
#     if not user_credit:
#         user_credit = Credit.objects.create(
#             user=user,
#             action_type='Credit',
#             used_credit=0,
#             ending_balance=0,
#             starting_balance=0,
#             comments='Initial credit record'
#         )

#     # Recent messages
#     recent_messages = ComposeMessageLine.objects.filter(
#         compose_message__user_id=user.user_id
#     ).order_by('-submit_time')[:10]

#     # Load routing
#     mapping = RoutingMapping.objects.filter(user=user).first()
#     routing = mapping.routing if mapping else None
#     connections = SmppConnection.objects.filter(bind_status="BOUND", is_alive=True) if routing else SmppConnection.objects.none()

#     if request.method == "POST":
#         sender_id = request.POST.get("sender_name")
#         sender_obj = Sender.objects.filter(sender_id=sender_id, user=user).first()
#         if not sender_obj:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "Invalid sender selected",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         actual_sender_name = sender_obj.sender_name

#         # Form fields
#         mobile_numbers_raw = request.POST.get("mobile_numbers")
#         template_id = request.POST.get("template_id")
#         peid = request.POST.get("peid")
#         tmid = request.POST.get("tmid")
#         message = request.POST.get("message", "")
#         send_type = request.POST.get("send_type", "batch")
#         template_identifier = request.POST.get("template_identifier", "")
#         text_type = request.POST.get("text_type", "english")
#         schedule = request.POST.get("schedule", "immediate")
#         smart_link = request.POST.get("smart_link", "disable")
#         smart_link_url = request.POST.get("smart_link_url", "")
#         remove_duplicates = request.POST.get("remove_duplicates", "enable")
#         flash_message = request.POST.get("flash_message", "disable")
#         whatsapp_url = request.POST.get("whatsapp_url", "")

#         # Basic validation
#         if not sender_id or not message or not mobile_numbers_raw:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "Sender, Numbers, and Message are required",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         # Parse numbers
#         mobile_numbers = re.split(r"[\s,]+", mobile_numbers_raw.strip())
#         mobile_numbers = [m for m in mobile_numbers if m]
#         if remove_duplicates == "enable":
#             mobile_numbers = list(dict.fromkeys(mobile_numbers))

#         if not mobile_numbers:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "No valid mobile numbers",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         # Credit calculation
#         chars = 160 if text_type == "english" else 70
#         parts_per_message = (len(message) + chars - 1) // chars if len(message) > 0 else 1
#         total_parts_needed = len(mobile_numbers) * parts_per_message

#         # Get user wallet
#         wallet = WalletService.get_user_wallet(user)
#         if not wallet:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "No wallet found for user. Please contact support.",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         # Prepare messages data
#         messages_data = [{"parts": parts_per_message, "reference_id": mob, "mobile_number": mob} for mob in mobile_numbers]

#         # Handle deduction based on wallet type
#         if wallet.deduction_type == "SUBMISSION":
#             deduct_res = WalletService.deduct_immediate(
#                 user=user,
#                 wallet=wallet,
#                 parts=total_parts_needed,
#                 reference_id=None,  # WalletService generates batch_id
#                 comments=f"Batch immediate deduction for {len(mobile_numbers)} messages"
#             )
#             if not deduct_res.get("ok"):
#                 return render(request, "submit_sm.html", {
#                     "connections": connections,
#                     "senders": senders,
#                     "error": deduct_res.get("message", "Unable to deduct credits"),
#                     "user_credit": user_credit,
#                     "recent_messages": recent_messages
#                 })
#             batch_request_id = deduct_res["credit"].reference_id  # ✅ Get batch_id

#         else:  # DELIVERED
#             reserve = WalletService.create_pending_reservation(
#                 user=user,
#                 wallet=wallet,
#                 total_parts=total_parts_needed,
#                 message_count=len(mobile_numbers),
#                 request_id=None,  # WalletService generates batch_id
#                 reference_ids=mobile_numbers,
#                 comments=f"Batch reservation for {len(mobile_numbers)} messages (DELIVERED deduction)"
#             )
#             if not reserve.get("ok"):
#                 return render(request, "submit_sm.html", {
#                     "connections": connections,
#                     "senders": senders,
#                     "error": reserve.get("message", "Unable to reserve credits"),
#                     "user_credit": user_credit,
#                     "recent_messages": recent_messages
#                 })
#             batch_request_id = reserve["pending"].reference_id  # ✅ Get batch_id

#         # Collect TLVs
#         extra_tlvs = {}
#         for key in request.POST:
#             if key.startswith("tlv_tag_"):
#                 idx = key.split("_")[-1]
#                 tag_val = request.POST.get(key)
#                 val_val = request.POST.get(f"tlv_value_{idx}")
#                 if tag_val and val_val:
#                     try:
#                         extra_tlvs[tagname_to_int(tag_val)] = val_val
#                     except Exception:
#                         pass

#         # Get active SMPP session
#         lines = RoutingLine.objects.filter(
#             rout=routing,
#             smpp__bind_status="BOUND",
#             smpp__is_alive=True
#         ).select_related("smpp")

#         if not lines.exists():
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "No active SMPP lines",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         chosen_smpp = lines.first().smpp
#         session = SMPPSessionManager.get(chosen_smpp.smpp_id) or SMPPSessionManager.start_session(chosen_smpp.smpp_id)
#         if not session:
#             return render(request, "submit_sm.html", {
#                 "connections": connections,
#                 "senders": senders,
#                 "error": "Unable to create SMPP session",
#                 "user_credit": user_credit,
#                 "recent_messages": recent_messages
#             })

#         if not getattr(session, "dlr_listener_started", False):
#             from .smpp_dlr_manager import DLRListener
#             listener = DLRListener(session)
#             listener.start()
#             session.dlr_listener_started = True

#         # SMPP config
#         smpp_config = {
#             "source_addr_ton": chosen_smpp.source_addr_ton or 5,
#             "source_addr_npi": chosen_smpp.source_addr_npi or 0,
#             "dest_addr_ton": chosen_smpp.dest_addr_ton or 1,
#             "dest_addr_npi": chosen_smpp.dest_addr_npi or 1,
#             "registered_delivery": 1,
#             "pe_id": peid,
#             "template_id": template_id,
#         }

#         if flash_message == "enable":
#             smpp_config["esm_class"] = 16

#         # SAVE MAIN COMPOSE MESSAGE
#         with transaction.atomic():
#             compose_msg = ComposeMessage.objects.create(
#                 user_id=user.user_id,
#                 send_type=send_type,
#                 sender_id=sender_id,
#                 message_type="text",
#                 template_id=template_id,
#                 template_list=template_identifier,
#                 smart_link_flag=(smart_link == "enable"),
#                 remove_duplications_flag=(remove_duplicates == "enable"),
#                 send_as_flag_messsage_flag=(flash_message == "enable"),
#                 schedule=schedule,
#                 whatsapp_url=whatsapp_url,
#                 start_date=timezone.now(),
#                 end_date=timezone.now() if schedule == "immediate" else None,
#                 attributes_1=smart_link_url if smart_link == "enable" else "",
#                 attributes_2=text_type,
#                 attributes_3=f"Parts/message: {parts_per_message}",
#                 attributes_4=f"Length: {len(message)}",
#                 attributes_5=f"SenderName: {actual_sender_name}",
#                 attributes_6=batch_request_id,
#                 attributes_7=json.dumps({
#                     "total_parts": total_parts_needed,
#                     "message_count": len(mobile_numbers),
#                     "deduction_type": wallet.deduction_type,
#                     "plan_type": wallet.plan_type
#                 }),
#                 create_by=user.username,
#                 last_updated_by=user.username
#             )

#         # SEND SMS ONE-BY-ONE
#         line_objs = []
#         success_count = 0
#         failed_count = 0

#         for mob in mobile_numbers:
#             tlvs = extra_tlvs.copy()
#             if chosen_smpp.dlt_enable:
#                 if peid and chosen_smpp.dlt_entity_id_tag:
#                     tlvs[tagname_to_int(chosen_smpp.dlt_entity_id_tag)] = peid
#                 if template_id and chosen_smpp.send_dlt_template_id and chosen_smpp.dlt_template_id_tag:
#                     tlvs[tagname_to_int(chosen_smpp.dlt_template_id_tag)] = template_id
#                 if tmid:
#                     tlvs[0x1402] = tmid
#             try:
#                 result = session.sms_sender.submit_sm_with_tlvs(
#                     source_addr=actual_sender_name,
#                     destination_addr=mob,
#                     message=message,
#                     smpp_config=smpp_config,
#                     tlvs_map=tlvs
#                 )
#                 message_id = result.get("message_id")
#                 sequence_number = result.get("sequence_number")
#                 success_count += 1

#                 line_objs.append(ComposeMessageLine(
#                     compose_message=compose_msg,
#                     mobile_number=mob,
#                     text_mes=message,
#                     sender=actual_sender_name,
#                     receiver=mob,
#                     content=message,
#                     message_id=message_id,
#                     sequence_number=sequence_number,
#                     submit_time=timezone.now(),
#                     status=result.get("command_status_text", "SUBMITTED"),
#                     encoding=text_type,
#                     content_id=template_id,
#                     tmid=tmid,
#                     parts=parts_per_message,
#                     attributes_1=message_id or "",
#                     attributes_2=batch_request_id,
#                     attributes_3=f"SMSC: {chosen_smpp.connect_name}",
#                     attributes_4=f"Encoding: {text_type}",
#                     attributes_5=f"SmartLink: {smart_link}",
#                     attributes_6=json.dumps({
#                         "submitted": True,
#                         "wallet_deduction_type": wallet.deduction_type
#                     }),
#                     create_by=user.username
#                 ))
#             except Exception as e:
#                 failed_count += 1
#                 line_objs.append(ComposeMessageLine(
#                     compose_message=compose_msg,
#                     mobile_number=mob,
#                     text_mes=message,
#                     sender=actual_sender_name,
#                     receiver=mob,
#                     content=message,
#                     status="FAILED",
#                     reason=str(e)[:2000],
#                     submit_time=timezone.now(),
#                     parts=parts_per_message,
#                     attributes_1="",
#                     attributes_2=batch_request_id,
#                     create_by=user.username
#                 ))
#                 log.exception("Failed to submit to %s: %s", mob, e)

#         if line_objs:
#             ComposeMessageLine.objects.bulk_create(line_objs)

#         # Refund failed messages if SUBMISSION
#         if wallet.deduction_type == "SUBMISSION" and failed_count > 0:
#             refund_parts = failed_count * parts_per_message
#             if refund_parts > 0:
#                 WalletService.create_refund_entry(
#                     user=user,
#                     wallet=wallet,
#                     request_id=batch_request_id,
#                     refund_parts=refund_parts,
#                     reason=f"Refund for {failed_count} failed messages",
#                     comments=f"Auto-refund for failed submissions in batch {batch_request_id}"
#                 )

#         # Refresh user credit
#         user_credit = Credit.objects.filter(user=user).order_by('-credit_id').first()
#         recent_messages = ComposeMessageLine.objects.filter(
#             compose_message__user_id=user.user_id
#         ).order_by('-submit_time')[:10]

#         # Return to UI
#         return render(request, "submit_sm.html", {
#             "connections": connections,
#             "senders": senders,
#             "result": f"✔ Successfully submitted {success_count} messages (failed: {failed_count}) using Sender: {actual_sender_name}",
#             "message_id": batch_request_id,
#             "status_text": f"Submitted {success_count}/{len(mobile_numbers)}",
#             "seq_number": batch_request_id,
#             "user_credit": user_credit,
#             "recent_messages": recent_messages
#         })

#     # GET request
#     return render(request, "submit_sm.html", {
#         "connections": connections,
#         "senders": senders,
#         "user_credit": user_credit,
#         "recent_messages": recent_messages
#     })

# -----------------------------
# AJAX Templates
# -----------------------------
@login_required

def get_templates_for_sender(request):
    sender_id = request.GET.get("sender_id")
    templates = get_accessible_templates_queryset(request.user).filter(sender_id=sender_id).values(
        "id", "template_identifier", "dlt_template_id", "message_template"  # Add this field
    )
    return JsonResponse(list(templates), safe=False)


@login_required
def get_group_contacts(request):
    group_id = request.GET.get('group_id')
    try:
        group = GroupHeader.objects.get(group_id=group_id, user=request.user)
        numbers = list(
            GroupLine.objects.filter(group=group)
            .values_list('mobile_number', flat=True)
        )
        return JsonResponse({'numbers': numbers, 'group_name': group.group_name, 'count': len(numbers)})
    except GroupHeader.DoesNotExist:
        return JsonResponse({'error': 'Group not found'}, status=404)

@login_required
def get_template_content(request):
    sender_id = request.GET.get("sender_name")
    template_identifier = request.GET.get("template_identifier")
    
    try:
        template = MessageTemplate.objects.get(
            sender_name=sender_id,
            template_identifier=template_identifier
        )
        return JsonResponse({
            'message_template': template.message_template,
            'dlt_template_id': template.dlt_template_id
        })
    except MessageTemplate.DoesNotExist:
        return JsonResponse({'error': 'Template not found'}, status=404)




from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.db import transaction as db_transaction
from decimal import Decimal
import json
from .models import Company, User, Wallet, Role, Credit

@login_required
def wallet_list(request):
    # Get current user and their role
    current_user = request.user
    user_profile = User.objects.filter(user_id=current_user.user_id).first()
    
    # Initialize variables
    wallets = Wallet.objects.none()
    companies = Company.objects.none()
    user_role = None
    user_balance = Decimal('0')
    user_has_wallet = False
    prepaid_count = 0
    postpaid_count = 0
    
    if not user_profile and not current_user.is_superuser:
        # Handle case where user doesn't have a User profile (and not Django super admin)
        wallets = Wallet.objects.none()
        companies = Company.objects.none()
    else:
        if current_user.is_superuser:
            # Django super admin can see all wallets
            wallets = Wallet.objects.select_related('company', 'user').all()
            companies = Company.objects.all()
            user_role = "Django Super Admin"
        elif user_profile:
            user_role = user_profile.role.role_name if user_profile.role else None
            
            # Filter based on user role
            if user_role == "Super Admin":
                # Company Super Admin can see all wallets in their company
                wallets = Wallet.objects.select_related('company', 'user').filter(
                    company=user_profile.company
                )
                companies = Company.objects.filter(company_id=user_profile.company.company_id)
            
            elif user_role == "Reseller":
                # Company Admin can see wallets they created or assigned to them
                wallets = Wallet.objects.select_related('company', 'user').filter(
                    company=user_profile.company,
                    user=user_profile  # Only their own wallet
                )
                companies = Company.objects.filter(company_id=user_profile.company.company_id)
            
            elif user_role == "Support Admin":
                # Support Admin can see wallets of support users and regular users in their company
                wallets = Wallet.objects.select_related('company', 'user').filter(
                    company=user_profile.company
                )
                companies = Company.objects.filter(company_id=user_profile.company.company_id)
            
            else:
                # Regular users can only see their own wallet
                wallets = Wallet.objects.select_related('company', 'user').filter(
                    user=user_profile
                )
                companies = Company.objects.filter(company_id=user_profile.company.company_id)
        
        # Get logged-in user's wallet balance
        if user_profile:
            user_wallet = Wallet.objects.filter(user=user_profile).first()
            if user_wallet:
                user_balance = user_wallet.balance
                user_has_wallet = True
    
    # Calculate wallet counts
    if hasattr(wallets, 'count'):
        total_wallets = wallets.count()
    else:
        total_wallets = len(list(wallets))
    
    # Count prepaid and postpaid wallets efficiently
    for wallet in wallets:
        if wallet.plan_type == "Prepaid":
            prepaid_count += 1
        elif wallet.plan_type == "Postpaid":
            postpaid_count += 1

    return render(request, "wallet_list.html", {
        "wallets": wallets,
        "companies": companies,
        "current_user_role": user_role,
        "is_django_superuser": current_user.is_superuser,
        "current_user_profile": user_profile,
        "user_wallet_balance": user_balance,
        "user_has_wallet": user_has_wallet,
        "total_wallets": total_wallets,
        "prepaid_count": prepaid_count,
        "postpaid_count": postpaid_count,
    })

# ------------------------------------------
# FETCH USERS FOR SELECTED COMPANY WITH ROLE FILTERING
# ------------------------------------------
@csrf_exempt
@login_required
def get_users_by_company(request):
    """
    Fetch users for a selected company with hierarchical role-based filtering
    Users can ONLY see users from their OWN company
    """
    company_id = request.GET.get("company_id")
    current_user = request.user
    
    if not company_id:
        return JsonResponse({"users": []})
    
    # 1. Get current user's profile
    user_profile = User.objects.filter(user_id=current_user.user_id).first()
    
    if not user_profile:
        return JsonResponse({"users": []})
    
    # Regular app users without an assigned company cannot query company users.
    if not current_user.is_superuser and not user_profile.company:
        return JsonResponse({"users": []})
    
    user_role = user_profile.role.role_name if user_profile.role else None
    
    # 2. IMPORTANT: Check if requested company matches user's company
    # Users can ONLY see users from their OWN company
    if not current_user.is_superuser and str(user_profile.company.company_id) != str(company_id):
        # Only Django Super Admin can see users from other companies
        return JsonResponse({"users": []})
    
    # 3. For Django Super Admin - Can see ALL users in any company (except themselves)
    if current_user.is_superuser:
        users = User.objects.filter(company_id=company_id).exclude(user_id=current_user.user_id).select_related('role')
        data = [{
            "user_id": u.user_id, 
            "username": u.username, 
            "role": u.role.role_name if u.role else "No Role",
            "email": u.email if u.email else ""
        } for u in users]
        return JsonResponse({"users": data})
    
    # 4. For regular users - they can only access their OWN company
    # Double-check: ensure they're accessing their own company
    if str(user_profile.company.company_id) != str(company_id):
        return JsonResponse({"users": []})
    
    # 5. Apply hierarchical role-based filtering within SAME company
    # Start with all users in the same company, excluding self
    users = User.objects.filter(company=user_profile.company).exclude(user_id=current_user.user_id).select_related('role')
    
    if user_role == "Super Admin":
        # Company Super Admin can see all users in their company EXCEPT other Super Admins
        users = users.exclude(role__role_name="Super Admin")
    
    elif user_role == "Support Admin":
        # Support Admin can see: Support Admin, Reseller, and Client users in same company
        users = users.filter(
            role__role_name__in=["Support Admin", "Reseller", "Client"]
        )
    
    elif user_role == "Reseller":
        # Reseller can see: Other Resellers and Client users in same company
        users = users.filter(
            role__role_name__in=["Reseller", "Client"]
        )
    
    elif user_role == "Client":
        # Client can see: Other Client users only in same company
        users = users.filter(role__role_name="Client")
    
    else:
        # Default: only see other users with same role in same company
        users = users.filter(role=user_profile.role) if user_profile.role else users.none()
    
    # 6. Format response data
    data = [{
        "user_id": u.user_id, 
        "username": u.username, 
        "role": u.role.role_name if u.role else "No Role",
        "email": u.email if u.email else ""
    } for u in users]
    
    return JsonResponse({"users": data})
# ------------------------------------------
# GET LOGGED IN USER WALLET BALANCE
# ------------------------------------------
@csrf_exempt
@login_required
def get_user_wallet_balance(request):
    current_user = request.user
    user_profile = User.objects.filter(user_id=current_user.user_id).first()
    
    print(f"DEBUG: Current user: {current_user.user_id}, User profile: {user_profile}")
    
    if not user_profile:
        return JsonResponse({"balance": 0, "has_wallet": False})
    
    # Get user's wallet
    wallet = Wallet.objects.filter(user=user_profile).first()
    
    print(f"DEBUG: User wallet: {wallet}")
    
    if wallet:
        return JsonResponse({
            "balance": float(wallet.balance),
            "has_wallet": True,
            "wallet_id": wallet.wallet_id,
            "plan_type": wallet.plan_type,
            "wallet_owner": wallet.user.username
        })
    else:
        return JsonResponse({"balance": 0, "has_wallet": False})


# ------------------------------------------
# CREATE CREDIT LOG ENTRY HELPER FUNCTION
# ------------------------------------------
def create_credit_log(from_wallet, to_wallet, amount, transaction_type, description, created_by, 
                      company=None, user=None, comments=""):
    """
    Helper function to create standardized credit log entries
    """
    # Determine action type based on amount
    action_type = 'Debit' if amount < 0 else 'Credit'
    
    # Determine which wallet's balance to track
    target_wallet = to_wallet if to_wallet else from_wallet
    if target_wallet:
        starting_balance = target_wallet.balance - amount if amount > 0 else target_wallet.balance + abs(amount)
        ending_balance = target_wallet.balance
    else:
        starting_balance = ending_balance = Decimal('0')
    
    # Create credit log
    credit_log = Credit.objects.create(
        company=company if company else (target_wallet.company if target_wallet else None),
        user=user if user else (target_wallet.user if target_wallet else None),
        wallet=target_wallet,
        from_wallet=from_wallet,
        to_wallet=to_wallet,
        action_type=action_type,
        transaction_type=transaction_type,
        amount=abs(amount),
        starting_balance=starting_balance,
        ending_balance=ending_balance,
        comments=comments,
        description=description,
        create_by=created_by,
        attributes_1=f"TX-{from_wallet.wallet_id if from_wallet else 'SYS'}-{to_wallet.wallet_id if to_wallet else 'SYS'}"  # Store transaction reference
    )
    
    return credit_log


# ------------------------------------------
# ADD WALLET WITH DEDUCTION LOGIC
# ------------------------------------------
import traceback
from decimal import Decimal
import json
from django.db import transaction as db_transaction

@csrf_exempt
@login_required
def add_wallet(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            print(f"DEBUG add_wallet: Received data: {data}")
            
            current_user = request.user
            user_profile = User.objects.filter(user_id=current_user.user_id).first()
            
            # Check if Django super admin
            is_django_superuser = current_user.is_superuser
            print(f"DEBUG add_wallet: Is Django super admin: {is_django_superuser}")
            
            if not user_profile and not is_django_superuser:
                print(f"DEBUG add_wallet: User profile not found for user_id: {current_user.user_id}")
                return JsonResponse({"status": "error", "message": "User profile not found!"})
            
            user_role = user_profile.role.role_name if user_profile and user_profile.role else None
            print(f"DEBUG add_wallet: User role: {user_role}, Is Django super admin: {is_django_superuser}")
            
            company_id = data.get("company")
            user_id = data.get("user")
            plan_type = data.get("plan_type")
            deduction_type = data.get("deduction_type")
            action_type = data.get("action_type")
            balance = Decimal(data.get("balance", "0"))
            
            print(f"DEBUG add_wallet: company_id={company_id}, user_id={user_id}, plan_type={plan_type}, balance={balance}")
            
            # Get the target user for wallet creation
            target_user = User.objects.filter(user_id=user_id).first()
            if not target_user:
                print(f"DEBUG add_wallet: Target user not found for user_id: {user_id}")
                return JsonResponse({"status": "error", "message": "Target user not found!"})
            
            # Check target user's role
            target_user_role = target_user.role.role_name if target_user.role else None
            print(f"DEBUG add_wallet: Target user role: {target_user_role}")
            
            # Start database transaction
            try:
                with db_transaction.atomic():
                    # Get creator's wallet (for deduction)
                    creator_wallet = None
                    if user_profile:
                        creator_wallet = Wallet.objects.filter(user=user_profile).first()
                    
                    print(f"DEBUG add_wallet: Creator wallet: {creator_wallet}")
                    
                    # Check for duplicate wallet
                    if Wallet.objects.filter(company_id=company_id, user_id=user_id).exists():
                        return JsonResponse({
                            "status": "error", 
                            "message": "Wallet already exists for this user!"
                        })
                    
                    # DEDUCTION LOGIC
                    deduction_amount = Decimal('0')
                    if plan_type == "Prepaid":
                        # Deduct for Prepaid wallets
                        deduction_amount = balance
                        
                        print(f"DEBUG add_wallet: Deduction amount: {deduction_amount}")
                        
                        # Django Super Admin: No deduction
                        if is_django_superuser:
                            deduction_amount = Decimal('0')
                            print("DEBUG add_wallet: Django super admin - no deduction")
                        
                        # Company Super Admin creating for Support Admin: Check balance
                        elif user_role == "Super Admin":
                            if not creator_wallet:
                                return JsonResponse({
                                    "status": "error", 
                                    "message": "You don't have a wallet. Please create one first or contact your administrator."
                                })
                            
                            if creator_wallet.balance < deduction_amount:
                                return JsonResponse({
                                    "status": "error", 
                                    "message": f"Insufficient balance! Your balance: ₹{creator_wallet.balance}, Required: ₹{deduction_amount}"
                                })
                    
                    # Create the new wallet
                    new_wallet = Wallet.objects.create(
                        company_id=company_id,
                        user_id=user_id,
                        plan_type=plan_type,
                        deduction_type=deduction_type,
                        action_type=action_type,
                        balance=balance if plan_type == "Prepaid" else Decimal('99999999999999999'),
                        create_by=str(request.user),
                    )
                    print(f"DEBUG add_wallet: New wallet created: {new_wallet.wallet_id}")
                    
                    # PROCESS DEDUCTION if applicable
                    if deduction_amount > 0 and creator_wallet:
                        # Deduct from appropriate wallet
                        creator_wallet.balance -= deduction_amount
                        creator_wallet.last_updated_by = str(request.user)
                        creator_wallet.save()
                        
                        print(f"DEBUG add_wallet: Deducted {deduction_amount} from creator wallet")
                        
                        # Create credit log for deduction
                        create_credit_log(
                            from_wallet=creator_wallet,
                            to_wallet=new_wallet,
                            amount=deduction_amount,
                            transaction_type='WALLET_CREATION',
                            description=f"Wallet creation for {target_user.username} ({plan_type}) - Amount: ₹{deduction_amount}",
                            created_by=str(request.user),
                            company=target_user.company,
                            user=target_user,
                            comments=f"New {plan_type} wallet created by {request.user.username}"
                        )
                    elif is_django_superuser:
                        # Django super admin - no deduction, just create credit log
                        create_credit_log(
                            from_wallet=None,
                            to_wallet=new_wallet,
                            amount=balance if plan_type == "Prepaid" else Decimal('99999999999999999'),
                            transaction_type='WALLET_CREATION',
                            description=f"Wallet creation by Django Super Admin for {target_user.username} ({plan_type})",
                            created_by=str(request.user),
                            company=target_user.company,
                            user=target_user,
                            comments=f"New {plan_type} wallet created by Django Super Admin"
                        )
                    elif plan_type == "Postpaid":
                        # No deduction case for Postpaid
                        create_credit_log(
                            from_wallet=None,
                            to_wallet=new_wallet,
                            amount=Decimal('0'),
                            transaction_type='WALLET_CREATION',
                            description=f"Postpaid wallet creation for {target_user.username}",
                            created_by=str(request.user),
                            company=target_user.company,
                            user=target_user,
                            comments=f"New Postpaid wallet created"
                        )
                    
                    # Prepare response message
                    response_message = f"Wallet created successfully!"
                    if deduction_amount > 0:
                        response_message += f" ₹{deduction_amount} deducted from your wallet."
                    elif plan_type == "Postpaid":
                        response_message += " Postpaid wallet created with unlimited balance."
                    elif is_django_superuser:
                        response_message += " No deduction (Django Super Admin)."
                    
                    print(f"DEBUG add_wallet: Success response: {response_message}")
                    
                    return JsonResponse({
                        "status": "success", 
                        "message": response_message
                    })
                    
            except Exception as e:
                print(f"DEBUG add_wallet: Transaction error: {str(e)}")
                traceback.print_exc()
                return JsonResponse({
                    "status": "error", 
                    "message": f"Transaction error: {str(e)}"
                })
                
        except Exception as e:
            print(f"DEBUG add_wallet: General error: {str(e)}")
            traceback.print_exc()
            return JsonResponse({
                "status": "error", 
                "message": f"Server error: {str(e)}"
            })
    
    return JsonResponse({"status": "error", "message": "Invalid request"})


@csrf_exempt
@login_required
def edit_wallet(request, wallet_id):
    wallet = get_object_or_404(Wallet, wallet_id=wallet_id)
    
    current_user = request.user
    user_profile = User.objects.filter(user_id=current_user.user_id).first()
    
    if not user_profile:
        return JsonResponse({"status": "error", "message": "User profile not found!"})
    
    user_role = user_profile.role.role_name if user_profile.role else None
    
    # Check permissions (unchanged)
    if not current_user.is_superuser:
        if user_role == "Super Admin":
            if wallet.company.company_id != user_profile.company.company_id:
                return JsonResponse({"status": "error", "message": "You can only edit wallets in your own company!"})
        elif user_role == "Admin" or user_role == "Support Admin":
            if user_role == "Admin" and wallet.user.user_id != current_user.user_id:
                return JsonResponse({"status": "error", "message": "You can only edit your own wallet!"})
            elif user_role == "Support Admin" and wallet.company.company_id != user_profile.company.company_id:
                return JsonResponse({"status": "error", "message": "You can only edit wallets in your company!"})
        else:
            if wallet.user.user_id != current_user.user_id:
                return JsonResponse({"status": "error", "message": "You can only edit your own wallet!"})

    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            new_plan_type = data.get("plan_type")
            deduction_type = data.get("deduction_type")
            action_type = data.get("action_type")
            entered_amount = Decimal(data.get("amount")) if data.get("amount") else Decimal('0')
            old_balance = wallet.balance
            
            if entered_amount < 0:
                return JsonResponse({
                    "status": "error",
                    "message": "Amount cannot be negative!"
                })
            
            if action_type not in ["Credit", "Debit"]:
                return JsonResponse({
                    "status": "error",
                    "message": "Please select a valid action type!"
                })
            
            if action_type == "Credit":
                new_balance = old_balance + entered_amount
            else:
                new_balance = old_balance - entered_amount
                if new_balance < 0:
                    return JsonResponse({
                        "status": "error",
                        "message": f"Insufficient wallet balance! Current balance: ₹{old_balance}"
                    })
            
            if new_plan_type == "Prepaid" and new_balance < 0:
                return JsonResponse({
                    "status": "error", 
                    "message": "Balance cannot be negative for Prepaid wallets!"
                })
            
            # ========== FIXED: PROCESSING WALLET LOGIC ==========
            processing_wallet = None
            
            if not current_user.is_superuser:
                # Get the logged-in user's wallet
                logged_in_user_wallet = Wallet.objects.filter(user=user_profile).first()
                
                if not logged_in_user_wallet:
                    return JsonResponse({
                        "status": "error", 
                        "message": "You don't have a wallet to process transactions!"
                    })
                
                # For Support Admin editing someone else's wallet
                if user_role == "Support Admin" and wallet.user.user_id != current_user.user_id:
                    # Use Support Admin's own wallet for processing
                    processing_wallet = logged_in_user_wallet
                    print(f"DEBUG: Support Admin editing other user. Using Admin wallet: {processing_wallet.user.username}, Balance: {processing_wallet.balance}")
                
                # For anyone editing their own wallet
                elif wallet.user.user_id == current_user.user_id:
                    # Use their own wallet (no transfers needed)
                    processing_wallet = wallet
                    print(f"DEBUG: User editing own wallet. Using own wallet: {processing_wallet.user.username}, Balance: {processing_wallet.balance}")
                
                # For Admin (not Support Admin) or regular users
                else:
                    processing_wallet = logged_in_user_wallet
                    print(f"DEBUG: Regular user editing. Using own wallet: {processing_wallet.user.username}, Balance: {processing_wallet.balance}")
            
            with db_transaction.atomic():
                old_plan_type = wallet.plan_type
                
                print(f"DEBUG: Editing wallet for {wallet.user.username}")
                print(f"DEBUG: Old balance: {old_balance}, Amount entered: {entered_amount}, Action: {action_type}, New balance: {new_balance}")
                print(f"DEBUG: Processing wallet: {processing_wallet.user.username if processing_wallet else 'None'}, Balance: {processing_wallet.balance if processing_wallet else 'N/A'}")
                
                # Store original values
                original_values = {
                    'plan_type': old_plan_type,
                    'balance': old_balance,
                    'deduction_type': wallet.deduction_type,
                    'action_type': wallet.action_type
                }
                
                # Calculate amount to add/deduct
                amount_to_process = new_balance - old_balance
                print(f"DEBUG: Amount to process: {amount_to_process}")
                
                # Handle plan type changes
                if old_plan_type == "Postpaid" and new_plan_type == "Postpaid":
                    # No balance changes for Postpaid
                    pass
                    
                elif old_plan_type == "Postpaid" and new_plan_type == "Prepaid":
                    # Postpaid → Prepaid: Need initial balance
                    if not current_user.is_superuser and processing_wallet:
                        # Check if enough balance
                        if processing_wallet.balance < new_balance:
                            return JsonResponse({
                                "status": "error", 
                                "message": f"Insufficient balance in your wallet! Available: ₹{processing_wallet.balance}, Required: ₹{new_balance}"
                            })
                        
                        # Deduct full new balance
                        processing_wallet.balance -= new_balance
                        processing_wallet.last_updated_by = str(request.user)
                        processing_wallet.save()
                        
                        print(f"DEBUG: Deducted ₹{new_balance} from {processing_wallet.user.username}")
                        
                        # Create credit log
                        create_credit_log(
                            from_wallet=processing_wallet,
                            to_wallet=wallet,
                            amount=new_balance,
                            transaction_type='BALANCE_UPDATE',
                            description=f"Plan changed to Prepaid - Added: ₹{new_balance}",
                            created_by=str(request.user),
                            company=wallet.company,
                            user=wallet.user,
                            comments=f"Plan changed from Postpaid to Prepaid"
                        )
                
                elif old_plan_type == "Prepaid" and new_plan_type == "Postpaid":
                    # Prepaid → Postpaid: Refund old balance
                    if old_balance > 0 and not current_user.is_superuser and processing_wallet:
                        # Only refund if NOT editing own wallet
                        if processing_wallet.wallet_id != wallet.wallet_id:
                            processing_wallet.balance += old_balance
                            processing_wallet.last_updated_by = str(request.user)
                            processing_wallet.save()
                            
                            print(f"DEBUG: Refunded ₹{old_balance} to {processing_wallet.user.username}")
                            
                            create_credit_log(
                                from_wallet=wallet,
                                to_wallet=processing_wallet,
                                amount=old_balance,
                                transaction_type='REFUND',
                                description=f"Plan changed to Postpaid - Refunded: ₹{old_balance}",
                                created_by=str(request.user),
                                company=wallet.company,
                                user=wallet.user,
                                comments=f"Plan changed from Prepaid to Postpaid"
                            )
                
                elif old_plan_type == "Prepaid" and new_plan_type == "Prepaid":
                    # Prepaid → Prepaid: Handle balance update
                    
                    if amount_to_process > 0:
                        # INCREASING balance
                        if not current_user.is_superuser and processing_wallet:
                            # If editing own wallet, no deduction needed
                            if processing_wallet.wallet_id == wallet.wallet_id:
                                print(f"DEBUG: Editing own wallet - no deduction needed")
                                # Just update balance, no transfer
                            else:
                                # Check if enough balance
                                if processing_wallet.balance < amount_to_process:
                                    return JsonResponse({
                                        "status": "error", 
                                        "message": f"Insufficient balance in your wallet! Available: ₹{processing_wallet.balance}, Required: ₹{amount_to_process}"
                                    })
                                
                                # Deduct the difference
                                processing_wallet.balance -= amount_to_process
                                processing_wallet.last_updated_by = str(request.user)
                                processing_wallet.save()
                                
                                print(f"DEBUG: Deducted ₹{amount_to_process} from {processing_wallet.user.username}")
                                
                                create_credit_log(
                                    from_wallet=processing_wallet,
                                    to_wallet=wallet,
                                    amount=amount_to_process,
                                    transaction_type='BALANCE_UPDATE',
                                    description=f"Balance increased by ₹{amount_to_process}",
                                    created_by=str(request.user),
                                    company=wallet.company,
                                    user=wallet.user,
                                    comments=f"Balance updated from ₹{old_balance} to ₹{new_balance}"
                                )
                    
                    elif amount_to_process < 0:
                        # DECREASING balance
                        if not current_user.is_superuser and processing_wallet:
                            refund_amount = abs(amount_to_process)
                            
                            # If editing own wallet, no refund needed
                            if processing_wallet.wallet_id != wallet.wallet_id:
                                processing_wallet.balance += refund_amount
                                processing_wallet.last_updated_by = str(request.user)
                                processing_wallet.save()
                                
                                print(f"DEBUG: Refunded ₹{refund_amount} to {processing_wallet.user.username}")
                                
                                create_credit_log(
                                    from_wallet=wallet,
                                    to_wallet=processing_wallet,
                                    amount=refund_amount,
                                    transaction_type='REFUND',
                                    description=f"Balance decreased by ₹{refund_amount}",
                                    created_by=str(request.user),
                                    company=wallet.company,
                                    user=wallet.user,
                                    comments=f"Balance updated from ₹{old_balance} to ₹{new_balance}"
                                )
                
                # Update the target wallet
                wallet.plan_type = new_plan_type
                wallet.deduction_type = deduction_type
                wallet.action_type = action_type
                
                if new_plan_type == "Postpaid":
                    wallet.balance = Decimal('0')  # Or unlimited marker
                else:
                    wallet.balance = new_balance
                
                wallet.last_updated_by = str(request.user)
                wallet.save()
                
                print(f"DEBUG: Success! Target wallet updated to ₹{wallet.balance}")
                
                return JsonResponse({
                    "status": "success", 
                    "message": "Wallet updated successfully!",
                    "changes": {
                        "old": original_values,
                        "new": {
                            "plan_type": new_plan_type,
                            "balance": wallet.balance,
                            "amount": entered_amount,
                            "deduction_type": deduction_type,
                            "action_type": action_type
                        }
                    }
                })
                
        except Exception as e:
            print(f"DEBUG ERROR: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({
                "status": "error", 
                "message": f"Error updating wallet: {str(e)}"
            })

    return JsonResponse({"status": "error", "message": "Invalid request"})

# ------------------------------------------
# GET USER TRANSACTION HISTORY
# ------------------------------------------
from django.db import models
@login_required
def get_transaction_history(request):
    current_user = request.user
    user_profile = User.objects.filter(user_id=current_user.user_id).first()
    
    if not user_profile:
        return JsonResponse({"transactions": []})
    
    # Get user's wallet
    user_wallet = Wallet.objects.filter(user=user_profile).first()
    
    if not user_wallet:
        return JsonResponse({"transactions": []})
    
    # Get transactions where user's wallet is involved
    transactions = Credit.objects.filter(
        models.Q(from_wallet=user_wallet) | models.Q(to_wallet=user_wallet)
    ).select_related('from_wallet__user', 'to_wallet__user', 'company').order_by('-created_date')[:50]
    
    transaction_data = []
    for txn in transactions:
        transaction_data.append({
            "id": txn.credit_id,
            "date": txn.created_date.strftime("%d %b %Y %H:%M"),
            "type": txn.transaction_type,
            "description": txn.description,
            "amount": float(txn.amount),
            "action": txn.action_type,
            "from_user": txn.from_wallet.user.username if txn.from_wallet else "System",
            "to_user": txn.to_wallet.user.username if txn.to_wallet else "System",
            "ending_balance": float(txn.ending_balance) if txn.ending_balance else 0,
        })
    
    return JsonResponse({"transactions": transaction_data})
# sms_app/views.py
import hashlib
from django.shortcuts import render, redirect
from .models import HashTable
from sms_app.models import User, Sender, SmppConnection

import hashlib
from django.shortcuts import render, redirect
from .models import HashTable
from sms_app.models import User, Sender, SmppConnection


def manage_hash(request):

    if request.method == "POST":

        user_id = request.POST.get("user_name")
        hash_type = request.POST.get("type")

        sender_id = None
        connection_id = None
        pe_id = None
        tmid = None
        hash_value = None

        # ===================== ON CASE =====================
        if hash_type == "ON":

            sender_id = request.POST.get("sender")
            connection_id = request.POST.get("connection")

            if sender_id and connection_id:

                sender = Sender.objects.get(sender_id=sender_id)
                connection = SmppConnection.objects.get(smpp_id=connection_id)

                # Take PE_ID from Sender table
                pe_id = sender.peid or ""

                # Take TM_ID from SmppConnection (DLT Telemarketer ID)
                tmid = connection.dlt_telemarketer_id or ""

                # Create SHA256 hash using PE_ID + TM_ID
                raw_string = f"{pe_id},{tmid}"
                hash_value = hashlib.sha256(raw_string.encode()).hexdigest()

        # ===================== OFF CASE =====================
        elif hash_type == "OFF":
            pass

        # ===================== PROVIDE_TM =====================
        elif hash_type == "PROVIDE_TM":

            pe_id = request.POST.get("provide_tm") or ""
            raw_string = pe_id
            hash_value = hashlib.sha256(raw_string.encode()).hexdigest()

        # ===================== PACKET_TM =====================
        elif hash_type == "PACKET_TM":

            tmid = request.POST.get("packet_tm") or ""
            raw_string = tmid
            hash_value = hashlib.sha256(raw_string.encode()).hexdigest()

        # ===================== SAVE =====================
        HashTable.objects.create(
            user_id=user_id,
            sender_id=sender_id,              # stores sender_id
            connection_id=connection_id,      # stores smpp_id
            type=hash_type,
            pe_id=pe_id,                      # stores sender.peid
            tmid=tmid,                        # stores dlt_telemarketer_id
            hash_value=hash_value,            # stores generated SHA hash
            created_by=request.user.username if request.user.is_authenticated else "Admin",
            updated_by=request.user.username if request.user.is_authenticated else "Admin",
        )

        return redirect("manage-hash")

    context = {
        "users": User.objects.all(),
        "senders": Sender.objects.filter(active_flag=True),
        "connections": SmppConnection.objects.all(),
        "hash_list": HashTable.objects.select_related(
            "user", "sender", "connection"
        ).order_by("-created_at")
    }

    return render(request, "manage_hash.html", context)

