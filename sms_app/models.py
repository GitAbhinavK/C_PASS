# Create your models here.
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone
from django.contrib.auth.base_user import BaseUserManager
from django.core.validators import URLValidator
from django.conf import settings

class Company(models.Model):
    company_id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True, null=True)
    mobile = models.CharField(max_length=20, blank=True, null=True)
    logo = models.ImageField(upload_to='logos/', blank=True, null=True)
    active_status = models.BooleanField(default=True)
    email = models.EmailField(max_length=255, unique=True)
    allowed_ip = models.GenericIPAddressField(protocol="both", unpack_ipv4=True, blank=True, null=True)
    # tfa_number = models.CharField(max_length=15, blank=True, null=True)

    domain_name = models.CharField(max_length=255,validators=[URLValidator()],blank=True,null=True)
    favicon = models.ImageField(upload_to='favicons/', blank=True, null=True)


    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)

    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    create_by = models.CharField(max_length=255, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class Role(models.Model):
    ROLE_CHOICES = [
        ('Super Admin', 'Super Admin'),
        ('Support Admin', 'Support Admin'),
        ('Reseller', 'Reseller'),
        ('Client', 'Client'),
    ]
    role_id = models.BigAutoField(primary_key=True)
    company = models.ForeignKey(
        'Company',  # refers to the Company model
        on_delete=models.CASCADE,
        related_name='roles'
    )
    role_name =  models.CharField(max_length=50, choices=ROLE_CHOICES)
    flag = models.BooleanField(default=True)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)

    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    create_by = models.CharField(max_length=255, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.company:
            return f"{self.company.name} - {self.name}"
        return self.name




class UserManager(BaseUserManager):
    def create_user(self, username, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email must be set")
        email = self.normalize_email(email)

        # 🔹 Remove company enforcement
        # Only assign if explicitly passed
        company = extra_fields.pop("company", None)

        user = self.model(username=username, email=email, company=company, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(username, email, password, **extra_fields)




class User(AbstractBaseUser, PermissionsMixin):
    user_id = models.BigAutoField(primary_key=True)

    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='users',
        db_column='company_id'
    )

    role = models.ForeignKey(
        'Role',
        on_delete=models.SET_NULL,
        related_name='users',
        null=True,
        db_column='role_id'
    )

    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(max_length=255, unique=True)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    mobile_number = models.CharField(max_length=20, blank=True, null=True)
    tfa_number = models.CharField(max_length=20, blank=True, null=True)
    join_date = models.DateField(auto_now_add=True)
    user_status = models.CharField(max_length=50, blank=True, null=True)

    # Permissions fields
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # Other fields
    address = models.TextField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    region = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    SMS_ACCOUNT_TYPE = [
        ('Transaction', 'Transaction'),
        ('Promotional', 'Promotional'), 
        ('Any', 'Any'), 
    ]
    sms_account_type = models.CharField(max_length=50, blank=True, null=True)
    sms_posting_start_hour = models.TimeField(blank=True, null=True)
    sms_posting_end_hour = models.TimeField(blank=True, null=True)
    smpp_enabled = models.BooleanField(default=False)
    hide_pricing = models.BooleanField(default=False)
    smpp_bind_mode = models.CharField(max_length=50, blank=True, null=True)
    smpp_sessions = models.IntegerField(blank=True, null=True)
    smpp_tps = models.IntegerField(blank=True, null=True)
    smpp_port = models.IntegerField(blank=True, null=True)

    create_by = models.CharField(max_length=255, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)
    pin_code = models.CharField(max_length=20, blank=True, null=True)

    objects = UserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']

    def __str__(self):
        return self.username


class Wallet(models.Model):
    wallet_id = models.BigAutoField(primary_key=True)

    # Links wallet to User & Company (org)
    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        related_name='wallets',
        db_column='org_id'
    )
    user = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='wallet',
        db_column='user_id'
    )

    PLAN_TYPE = [
        ('Prepaid', 'Prepaid'),
        ('Postpaid', 'Postpaid'),
    ]
    ACTION_TYPE = [
        ('Credit', 'Credit'),
        ('Debit', 'Debit'),
        
    ]

    DEDUCTION_TYPE = [
        ('SUBMISSION', 'SUBMISSION'),
        ('DELIVERED', 'DELIVERED'),
        
    ]

    plan_type = models.CharField(max_length=50, choices=PLAN_TYPE, blank=True, null=True)
    deduction_type = models.CharField(max_length=50, choices=DEDUCTION_TYPE, blank=True, null=True)
    action_type = models.CharField(max_length=50, blank=True, null=True,choices=ACTION_TYPE,)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # Attributes
    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    # Audit fields
    create_by = models.CharField(max_length=255, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} Wallet - {self.balance}"


class Credit(models.Model):
    ACTION_TYPES = [
        ('Credit', 'Credit'),
        ('Debit', 'Debit'),
    ]
    
    TRANSACTION_TYPES = [
        ('WALLET_CREATION', 'Wallet Creation'),
        ('BALANCE_UPDATE', 'Balance Update'),
        ('DEDUCTION', 'Deduction'),
        ('REFUND', 'Refund'),
        ('WALLET_TRANSFER', 'Wallet Transfer'),
    ]
    
    STATUS_TYPES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('REVERSED', 'Reversed'),
    ]

    credit_id = models.BigAutoField(primary_key=True)

    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        related_name='credit_logs',
        db_column='org_id',
        null=True, blank=True
    )

    user = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='credit_logs',
        db_column='user_id',
        null=True, blank=True
    )

    wallet = models.ForeignKey(
        'Wallet',
        on_delete=models.CASCADE,
        related_name='transactions',
        null=True,
        blank=True
    )
    
    # New fields for wallet-to-wallet transactions
    from_wallet = models.ForeignKey(
        'Wallet',
        on_delete=models.SET_NULL,
        related_name='from_credits',
        null=True,
        blank=True
    )
    
    to_wallet = models.ForeignKey(
        'Wallet',
        on_delete=models.SET_NULL,
        related_name='to_credits',
        null=True,
        blank=True
    )

    action_type = models.CharField(max_length=50, choices=ACTION_TYPES, blank=True, null=True)
    
    # New: Transaction type for better categorization
    transaction_type = models.CharField(max_length=50, choices=TRANSACTION_TYPES, blank=True, null=True)
    
    # New: Transaction status
    status = models.CharField(max_length=20, choices=STATUS_TYPES, default='COMPLETED')

    reference_id = models.CharField(max_length=255, blank=True, null=True)

    # Transaction values
    used_credit = models.PositiveIntegerField(default=0, null=True, blank=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0,  blank=True, null=True)
    ending_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0, blank=True, null=True)
    
    # New: Starting balance for better tracking
    starting_balance = models.DecimalField(max_digits=15, decimal_places=2, default=0, blank=True, null=True)

    # Free comments
    comments = models.TextField(blank=True, null=True)
    
    # New: Description field for transaction details
    description = models.TextField(blank=True, null=True)

    # Attributes 1–10
    attributes_1 = models.CharField(max_length=255, blank=True, null=True)  # Can store transaction ID or reference
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)  # Can store IP address
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)  # Can store user agent
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)  # Can store payment gateway info
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)  # Reserved
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)  # Reserved
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)  # Reserved
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)  # Reserved
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)  # Reserved
    attributes_10 = models.CharField(max_length=255, blank=True, null=True) # Reserved

    # Audit fields
    create_by = models.CharField(max_length=255, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"CR-{self.credit_id}: {self.user.username if self.user else 'System'} - {self.action_type} - ₹{self.amount}"
    
    def save(self, *args, **kwargs):
        # Auto-set action_type based on amount if not provided
        if not self.action_type:
            if self.amount and self.amount > 0:
                self.action_type = 'Credit'
            elif self.amount and self.amount < 0:
                self.action_type = 'Debit'
        
        # Auto-set transaction_type if not provided
        if not self.transaction_type:
            if self.from_wallet and self.to_wallet:
                self.transaction_type = 'WALLET_TRANSFER'
        
        super().save(*args, **kwargs)

    @property
    def username(self):
        return self.user.username if self.user else "System"
    
    @property
    def company_name(self):
        return self.company.name if self.company else "N/A"
    
    @property
    def total_credits(self):
        """Calculate total credits (positive amounts only)"""
        if self.action_type == 'Credit':
            return self.amount
        return 0
    
    @property
    def used_credits(self):
        """Calculate used credits (negative amounts only)"""
        if self.action_type == 'Debit':
            return abs(self.amount) if self.amount < 0 else 0
        return 0
    
    @property
    def available_credits(self):
        """Calculate available credits from ending balance"""
        return self.ending_balance or 0
    
    class Meta:
        ordering = ['-created_date']
        verbose_name = 'Credit Transaction'
        verbose_name_plural = 'Credit Transactions'



class Sender(models.Model):
    sender_id = models.BigAutoField(primary_key=True)  # Auto-increment ID
    user = models.ForeignKey('sms_app.User', on_delete=models.CASCADE, null=True, blank=True)

    peid = models.CharField(max_length=100, blank=True, null=True)  # PEID
    sender_name = models.CharField(max_length=255)  # SENDER_NAME
    active_flag = models.BooleanField(default=True)  # ACTIVE_FLAG
    start_date = models.DateField(default=timezone.now)  # START_DATE
    end_date = models.DateField(blank=True, null=True)  # END_DATE

    # Attributes 1-10 (optional fields)
    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    # Audit fields
    create_by = models.CharField(max_length=150, blank=True, null=True)  # CREATED BY
    created_date = models.DateTimeField(default=timezone.now)
    last_updated_by = models.CharField(max_length=150, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.sender_name} ({self.peid})" if self.peid else self.sender_name

class MessageTemplate(models.Model):
    id = models.BigAutoField(primary_key=True)  # Auto-increment ID
    user = models.ForeignKey('sms_app.User', on_delete=models.CASCADE,  blank=True, null=True)
    sender = models.ForeignKey('Sender', on_delete=models.CASCADE, blank=True, null=True)
    template_identifier = models.CharField(max_length=255)  # TEMPATE_IDENTIFIER
    # sender_id = models.BigIntegerField()  # SENDER_ID, can be FK to Sender table
    message_template = models.TextField()  # MESSAGE_TEMPATE
    dlt_template_id = models.CharField(max_length=255, blank=True, null=True)  # DLT_TEMPLATE_ID
    dlt_template_type = models.CharField(max_length=100, blank=True, null=True)  # DLT_TEMPALTE_TYPE
    VARIABLE_CHOICES = [
        ('enable', 'Enable'),
        ('disable', 'Disable'),
    ]
    variable_type = models.CharField(max_length=10, choices=VARIABLE_CHOICES, default='enable')
    # variable_type = models.CharField(models.CharField(max_length=10, choices=VARIABLE_CHOICES, default='enable'))  # VARIABLE TYPE
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(blank=True, null=True)

    # Attributes 1-10 (optional fields)
    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    # Audit fields
    create_by = models.CharField(max_length=150, blank=True, null=True)
    created_date = models.DateTimeField(default=timezone.now)
    last_updated_by = models.CharField(max_length=150, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'message_template'  # Optional: set table name
        ordering = ['id']

    def __str__(self):
        return self.template_identifier

class ComposeMessage(models.Model): 
    sms_id = models.BigAutoField(primary_key=True)
    group_id = models.BigIntegerField(blank=True, null=True)
    user_id = models.BigIntegerField(blank=True, null=True)

    send_type = models.CharField(max_length=50, blank=True, null=True)
    sender_id = models.CharField(max_length=100, blank=True, null=True)
    message_type = models.CharField(max_length=50, blank=True, null=True)

    smart_link_flag = models.BooleanField(default=False)
    remove_duplications_flag = models.BooleanField(default=False)
    send_as_flag_messsage_flag = models.BooleanField(default=False)

    template_id = models.BigIntegerField(blank=True, null=True)
    template_list = models.TextField(blank=True, null=True)

    schedule = models.CharField(max_length=100, blank=True, null=True)
    whatsapp_url = models.URLField(max_length=500, blank=True, null=True)

    start_date = models.DateTimeField(blank=True, null=True)
    end_date = models.DateTimeField(blank=True, null=True)

    attributes_1 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=2525, blank=True, null=True)
    attributes_4 = models.CharField(max_length=2552, blank=True, null=True)
    attributes_5 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=2255, blank=True, null=True)

    create_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)

    last_updated_by = models.CharField(max_length=100, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)


class ComposeMessageLine(models.Model):
    line_id = models.BigAutoField(primary_key=True)
    compose_message = models.ForeignKey(
        ComposeMessage, on_delete=models.CASCADE, related_name='lines',null=True, blank=True
    )  # Could be FK to SMS table if needed

    mobile_number = models.CharField(max_length=20, blank=True, null=True)
    text_mes = models.TextField(blank=True, null=True)

    sender = models.CharField(max_length=100, blank=True, null=True)
    receiver = models.CharField(max_length=100, blank=True, null=True)
    content = models.TextField(blank=True, null=True)

    submit_time = models.DateTimeField(blank=True, null=True)
    dlr_time = models.DateTimeField(blank=True, null=True)
    message_id = models.CharField(max_length=100, blank=True, null=True)
    account = models.CharField(max_length=100, blank=True, null=True)
    parts = models.IntegerField(blank=True, null=True)
    entity_id = models.CharField(max_length=100, blank=True, null=True)
    content_id = models.CharField(max_length=100, blank=True, null=True)
    campaign = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    reason = models.CharField(max_length=2255, blank=True, null=True)
    encoding = models.CharField(max_length=50, blank=True, null=True)
    smsc = models.CharField(max_length=100, blank=True, null=True)
    masked_reason = models.CharField(max_length=2255, blank=True, null=True)
    masked_status = models.CharField(max_length=50, blank=True, null=True)
    tmid = models.CharField(max_length=100, blank=True, null=True)
    sequence_number = models.BigIntegerField(null=True, blank=True, db_index=True)

    attributes_1 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=2255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=2255, blank=True, null=True)

    create_by = models.CharField(max_length=100, blank=True, null=True)

    last_updated_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['message_id']),
            models.Index(fields=['status']),
            models.Index(fields=['submit_time']),
        ]



from django.db import models
from django.utils import timezone

class GroupHeader(models.Model):
    group_id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey('sms_app.User', on_delete=models.CASCADE, blank=True, null=True)
    group_name = models.CharField(max_length=255)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(blank=True, null=True)

    # Optional attributes
    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    # Audit fields
    create_by = models.CharField(max_length=150, blank=True, null=True)
    created_date = models.DateTimeField(default=timezone.now)
    last_updated_by = models.CharField(max_length=150, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'group_header'
        ordering = ['group_id']

    def __str__(self):
        return self.group_name


class GroupLine(models.Model):
    group_line_id = models.BigAutoField(primary_key=True)
    group = models.ForeignKey(GroupHeader, on_delete=models.CASCADE, related_name='lines')
    contact_name = models.CharField(max_length=255, blank=True, null=True)
    mobile_number = models.CharField(max_length=15)

    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    create_by = models.CharField(max_length=150, blank=True, null=True)
    created_date = models.DateTimeField(default=timezone.now)
    last_updated_by = models.CharField(max_length=150, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'group_line'
        ordering = ['group_line_id']

    def __str__(self):
        return f"{self.contact_name or 'Contact'} - {self.mobile_number}"


from django.db import models
from django.utils import timezone
import pytz   # for timezone list


# sms_app/models.py
import pytz
from django.db import models
from django.utils import timezone

class SmppConnection(models.Model):
    smpp_id = models.BigAutoField(primary_key=True)

    # --- Connection Details ---
    connect_name = models.CharField(max_length=255, null=True, blank=True)
    ip = models.CharField(max_length=255, null=True, blank=True)
    tr_trx_port = models.IntegerField(null=True, blank=True, help_text="TX/TRX Port")
    rx_port = models.IntegerField(null=True, blank=True, help_text="RX Port")

    username = models.CharField(max_length=255, null=True, blank=True)
    password = models.CharField(max_length=255, null=True, blank=True)
    system_type = models.CharField(max_length=100, null=True, blank=True)

    BIND_TYPE_CHOICES = [
        ('transmitter', 'Transmitter'),
        ('receiver', 'Receiver'),
        ('transceiver', 'Transceiver'),
    ]
    bind_type = models.CharField(max_length=20, choices=BIND_TYPE_CHOICES, null=True, blank=True)

    # --- New Choice Fields Requested ---
    BILLING_METHOD_CHOICES = [
        ('submission', 'Submission'),
        ('delivery', 'Delivery'),
    ]
    billing_method = models.CharField(max_length=50, choices=BILLING_METHOD_CHOICES, null=True, blank=True)

    ACCOUNT_TYPE_CHOICES = [
        ('transactional', 'Transactional'),
        ('promotional', 'Promotional'),
    ]
    account_type = models.CharField(max_length=50, choices=ACCOUNT_TYPE_CHOICES, null=True, blank=True)

    # --- Performance Settings ---
    tps = models.IntegerField(null=True, blank=True)
    tx_sessions = models.IntegerField(null=True, blank=True)
    rx_sessions = models.IntegerField(null=True, blank=True)

    # --- Address Settings ---
    dest_addr_ton = models.IntegerField(null=True, blank=True)
    dest_addr_npi = models.IntegerField(null=True, blank=True)
    source_addr_ton = models.IntegerField(null=True, blank=True)
    source_addr_npi = models.IntegerField(null=True, blank=True)
    window_size = models.IntegerField(default=10, null=True, blank=True)


    # --- Timezone Dropdown (LOV of all time zones) ---
    TIMEZONE_CHOICES = [(tz, tz) for tz in pytz.all_timezones]
    delivery_report_timezone = models.CharField(max_length=100, choices=TIMEZONE_CHOICES, null=True, blank=True)

    YES_NO_CHOICES = [
        ('yes', 'Yes'),
        ('no', 'No'),
    ]
    override_delivery_time_flag = models.CharField(max_length=5, choices=YES_NO_CHOICES, null=True, blank=True)

    support_message_validity = models.BooleanField(default=False)

    DATE_FORMAT_CHOICES = [
        ('yyyy-mm-dd', 'YYYY-MM-DD'),
        ('dd-mm-yyyy', 'DD-MM-YYYY'),
        ('mm-dd-yyyy', 'MM-DD-YYYY'),
        ('yyyy/mm/dd', 'YYYY/MM/DD'),
    ]
    date_format_in_dlr = models.CharField(max_length=20, choices=DATE_FORMAT_CHOICES, null=True, blank=True)

    modify_time_zone = models.CharField(max_length=5, choices=YES_NO_CHOICES, null=True, blank=True)

    # --- DLT Settings ---
    dlt_enable = models.BooleanField(default=False)
    dlt_telemarketer_id = models.CharField(max_length=255, null=True, blank=True)
    send_dlt_template_id = models.BooleanField(default=False)
    reject_non_dlt_template_message = models.BooleanField(default=False)

    dlt_entity_id_tag = models.CharField(max_length=255, null=True, blank=True, default="tag_1400")
    dlt_template_id_tag = models.CharField(max_length=255, null=True, blank=True, default="tag_1401")
    dlt_telemarketer_id_tag = models.CharField(max_length=255, null=True, blank=True, default="tag_1402")

    BEHAVIOUR_CHOICES = [
        ('do_nothing', 'Do Nothing'),
        ('pass_tmid', 'Pass Only TMID'),
        ('plaintext_chain', 'Pass Chain in Plain Text'),
        ('hash_chain', 'Pass Chain in Hash'),
    ]
    dlt_telemarketer_tag_behaviour = models.CharField(
        max_length=50, choices=BEHAVIOUR_CHOICES, null=True, blank=True
    )

    smpp_connect_status = models.CharField(max_length=20, default="Active")
    # --- Error Maps and Extra Attributes ---
    error_code_map = models.TextField(null=True, blank=True)
    connection_mode = models.CharField(
        max_length=20,
        choices=[
            ("transmitter", "Transmitter"),
            ("receiver", "Receiver"),
            ("transceiver", "Transceiver"),
        ],
        default="transceiver"
    )

    bind_status = models.CharField(
        max_length=20,
        default="UNBOUND",
        choices=[
            ("BOUND","BOUND"),
            ("UNBOUND","UNBOUND"),
            ("FAILED","FAILED"),
            ("RETRYING","RETRYING")
        ]
    )

    # NEW FIELD
    keepalive_interval = models.IntegerField(default=30)
    bind_status = models.CharField(max_length=20, default="UNBOUND")   
    last_bind_time = models.DateTimeField(null=True, blank=True)

    is_alive = models.BooleanField(default=False)
    last_enquire_link_time = models.DateTimeField(null=True, blank=True)

    uptime_seconds = models.BigIntegerField(default=0)

    tx_active_sessions = models.IntegerField(default=0)
    rx_active_sessions = models.IntegerField(default=0)

    reconnect_attempts = models.IntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)


    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    # --- Metadata ---
    create_by = models.CharField(max_length=150, blank=True, null=True)
    created_date = models.DateTimeField(default=timezone.now)
    last_updated_by = models.CharField(max_length=150, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "smpp_connection"
        verbose_name = "SMPP Connection"
        verbose_name_plural = "SMPP Connections"
        ordering = ['-last_updated_date']

    def __str__(self):
        return self.connect_name or f"SMPP-{self.smpp_id}"



class Routing(models.Model):

    STRATEGY_CHOICES = [
        ('Dedicated', 'Dedicated'),
        ('Percentage', 'Percentage'),
        ('Round-Robin', 'Round-Robin'),
        ('Priority', 'Priority'),
    ]

    rout_id = models.BigAutoField(primary_key=True)

    name = models.CharField(max_length=255)

    strategy = models.CharField(
        max_length=50,
        choices=STRATEGY_CHOICES,
        default='Dedicated'
    )

    primary_connect = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )

    apply_rules = models.BooleanField(default=False)
    message_retry = models.BooleanField(default=False)
    reject_non_peid_messages = models.BooleanField(default=False)

    
    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    # --- Metadata ---
    create_by = models.CharField(max_length=150, blank=True, null=True)
    created_date = models.DateTimeField(default=timezone.now)
    last_updated_by = models.CharField(max_length=150, blank=True, null=True)
    last_updated_date = models.DateTimeField(auto_now=True)
    def __str__(self):
        return self.name


   # adjust app name

class RoutingLine(models.Model):
    rout_line_id = models.BigAutoField(primary_key=True)

    # FK to RoutingMapping (rout_id)
    rout = models.ForeignKey(
        Routing,
        on_delete=models.CASCADE,
        db_column="rout_id",
        related_name="routing_lines",
        null=True,
        blank=True
    )

    # FK to SmppConnection (smpp_id)
    smpp = models.ForeignKey(
        SmppConnection,
        on_delete=models.CASCADE,
        db_column="smpp_id",
        related_name="routing_lines",
        null=True,
        blank=True
    )

    # Percentage-based routing weight
    percentage_weight = models.IntegerField(null=True, blank=True)

    # Date range validity
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    # Extra 10 attributes
    attributes_1 = models.CharField(max_length=255, null=True, blank=True)
    attributes_2 = models.CharField(max_length=255, null=True, blank=True)
    attributes_3 = models.CharField(max_length=255, null=True, blank=True)
    attributes_4 = models.CharField(max_length=255, null=True, blank=True)
    attributes_5 = models.CharField(max_length=255, null=True, blank=True)
    attributes_6 = models.CharField(max_length=255, null=True, blank=True)
    attributes_7 = models.CharField(max_length=255, null=True, blank=True)
    attributes_8 = models.CharField(max_length=255, null=True, blank=True)
    attributes_9 = models.CharField(max_length=255, null=True, blank=True)
    attributes_10 = models.CharField(max_length=255, null=True, blank=True)

    # Metadata
    create_by = models.CharField(max_length=150, null=True, blank=True)
    created_date = models.DateTimeField(default=timezone.now)
    last_updated_by = models.CharField(max_length=150, null=True, blank=True)
    last_updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "routing_mapping_line"
        verbose_name = "Routing Mapping Line"
        verbose_name_plural = "Routing Mapping Lines"
        ordering = ['-last_updated_date']

    def __str__(self):
        return f"RouteLine-{self.rout_line_id}"
    
class RoutingMapping(models.Model):
    map_id = models.AutoField(primary_key=True)
    rout_name = models.CharField(max_length=255, blank=True, null=True)
    routing = models.ForeignKey(Routing, on_delete=models.CASCADE)
    # smpp = models.ForeignKey(SmppConnection, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    description = models.TextField(blank=True, null=True)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)

    
    
    attributes_1 = models.CharField(max_length=255, blank=True, null=True)
    attributes_2 = models.CharField(max_length=255, blank=True, null=True)
    attributes_3 = models.CharField(max_length=255, blank=True, null=True)
    attributes_4 = models.CharField(max_length=255, blank=True, null=True)
    attributes_5 = models.CharField(max_length=255, blank=True, null=True)
    attributes_6 = models.CharField(max_length=255, blank=True, null=True)
    attributes_7 = models.CharField(max_length=255, blank=True, null=True)
    attributes_8 = models.CharField(max_length=255, blank=True, null=True)
    attributes_9 = models.CharField(max_length=255, blank=True, null=True)
    attributes_10 = models.CharField(max_length=255, blank=True, null=True)

    create_by = models.CharField(max_length=100, blank=True, null=True)
    created_date = models.DateTimeField(blank=True, null=True)
    last_updated_by = models.CharField(max_length=100, blank=True, null=True)
    last_updated_date = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Map {self.map_id}"

class HashTable(models.Model):
    TYPE_CHOICES = [
        ('ON', 'ON'),
        ('OFF', 'OFF'),
        ('PROVIDE_TM', 'PROVIDE_TM'),  # Added this
        ('PACKET_TM', 'PACKET_TM'),
    ]

    hash_id = models.BigAutoField(primary_key=True)

    # Relationships
    user = models.ForeignKey(
        'sms_app.User',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_column='user_id'
    )
    sender = models.ForeignKey(
        'sms_app.Sender',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_column='sender_id'
    )
    connection = models.ForeignKey(
        'sms_app.SmppConnection',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_column='connection_id'
    )

    # Core fields
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, blank=True, null=True)
    pe_id = models.CharField(max_length=100, null=True, blank=True)
    tmid = models.CharField(max_length=100, null=True, blank=True)
    hash_value = models.CharField(max_length=255, null=True, blank=True)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    # Attributes 1–10
    attribute1 = models.CharField(max_length=255, null=True, blank=True)
    attribute2 = models.CharField(max_length=255, null=True, blank=True)
    attribute3 = models.CharField(max_length=255, null=True, blank=True)
    attribute4 = models.CharField(max_length=255, null=True, blank=True)
    attribute5 = models.CharField(max_length=255, null=True, blank=True)
    attribute6 = models.CharField(max_length=255, null=True, blank=True)
    attribute7 = models.CharField(max_length=255, null=True, blank=True)
    attribute8 = models.CharField(max_length=255, null=True, blank=True)
    attribute9 = models.CharField(max_length=255, null=True, blank=True)
    attribute10 = models.CharField(max_length=255, null=True, blank=True)

    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.CharField(max_length=255, null=True, blank=True)
    updated_by = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'hash_table'
        verbose_name = 'Hash Table'
        verbose_name_plural = 'Hash Tables'

    def __str__(self):
        return f"Hash {self.hash_id} - {self.type}"