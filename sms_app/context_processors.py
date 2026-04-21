 # sms_app/context_processors.py


# ------------------------------------------------------------
# Importing the Company model
#  This model represents the company entity in the database
#  It is assumed that a User may be linked to a Company
#  This import allows us to access company data
#  Used inside the context processor function
#  Required to fetch company details for templates
#  Helps show company info globally in UI
#  Avoids querying company repeatedly in views
#  Keeps template logic clean
# ------------------------------------------------------------
from .models import Company


def user_company_context(request):
    # --------------------------------------------------------
    # Initialize the variable to store company details
    #  Default value is None
    #  Prevents UnboundLocalError
    #  Used when user has no company
    #  Ensures safe return value
    #  Makes template handling easier
    #  Avoids conditional checks in template
    #  Improves readability
    #  Acts as fallback value
    # --------------------------------------------------------
    user_company = None

    # --------------------------------------------------------
    # Check whether the user is logged in
    #  Prevents anonymous user access
    #  Ensures security
    #  Avoids attribute errors
    #  Confirms authentication state
    #  Required for accessing user relations
    #  Django best practice
    #  Prevents unnecessary DB calls
    #  Improves performance
    # --------------------------------------------------------
    if request.user.is_authenticated and hasattr(request.user, 'company'):
        # ----------------------------------------------------
        # Assign the related company object to variable
        #  Fetches company using reverse relationship
        #  Uses OneToOne or ForeignKey mapping
        #  Stores company for template usage
        #  Prevents repeated queries
        #  Makes company globally available
        #  Cleaner UI rendering
        #  Improves maintainability
        #  Avoids logic in HTML
        # ----------------------------------------------------
        user_company = request.user.company

    # --------------------------------------------------------
    # Return company information as context dictionary
    #  Context processors must return dict
    #  Key becomes template variable
    #  Accessible in all templates
    #  No need to pass from views
    #  Improves code reuse
    #  Centralized logic
    #  Django template friendly
    #  Standard context processor pattern
    # --------------------------------------------------------
    return {'user_company': user_company}


# ------------------------------------------------------------
# Import Role and Wallet models
#  Role defines user permission levels
#  Wallet stores credit balance
#  Required for role assignment logic
#  Used in multiple context processors
#  Keeps imports organized
#  Avoids circular imports
#  Makes role access global
#  Supports authorization UI
# ------------------------------------------------------------
from .models import Role, Wallet


def role_context(request):
    # --------------------------------------------------------
    # Check authentication before processing roles
    #  Anonymous users have no roles
    #  Improves security
    #  Prevents null access
    #  Stops unnecessary DB queries
    #  Required for permission checks
    #  Django best practice
    #  Keeps logic clean
    #  Prevents crashes
    # --------------------------------------------------------
    if not request.user.is_authenticated:
        return {}

    # --------------------------------------------------------
    # Store logged-in user object
    #  Avoids repeated request.user calls
    #  Improves readability
    #  Cleaner logic
    #  Easy role access
    #  Helps debugging
    #  Performance friendly
    #  Standard practice
    #  Code consistency
    # --------------------------------------------------------
    user = request.user

    # --------------------------------------------------------
    # Initialize roles as empty queryset
    #  Prevents None errors
    #  Safe default value
    #  Avoids conditional checks later
    #  Django queryset compatible
    #  Template-friendly
    #  Secure default
    #  No permission by default
    #  Clean fallback
    # --------------------------------------------------------
    roles = Role.objects.none()

    # --------------------------------------------------------
    # Check if Django superuser
    #  Superuser bypasses role restrictions
    #  Has full system access
    #  Django built-in privilege
    #  Independent of Role table
    #  Used for admin-level access
    #  Simplifies logic
    #  Security aware
    #  Clear hierarchy
    # --------------------------------------------------------
    if user.is_superuser:
        roles = Role.objects.all()

    # --------------------------------------------------------
    # If user has a role assigned
    #  Prevents AttributeError
    #  Ensures role-based logic
    #  Supports hierarchical access
    #  Improves authorization
    #  Maintains RBAC rules
    #  Cleaner role checks
    #  Safer logic
    #  Extensible design
    # --------------------------------------------------------
    elif user.role:
        role_name = user.role.role_name

        # Super Admin → Support Admin
        if role_name == 'Super Admin':
            roles = Role.objects.filter(role_name='Support Admin')

        # Support Admin → Reseller + Client
        elif role_name == 'Support Admin':
            roles = Role.objects.filter(role_name__in=['Reseller', 'Client'])

        # Reseller → Reseller + Client
        elif role_name == 'Reseller':
            roles = Role.objects.filter(role_name__in=['Reseller', 'Client'])

        # Client → No role assignment
        elif role_name == 'Client':
            roles = Role.objects.none()

        # Unknown role → No access
        else:
            roles = Role.objects.none()

    # --------------------------------------------------------
    # Return roles allowed for assignment
    #  Used in templates
    #  Powers dropdowns
    #  Centralized permission logic
    #  Avoids view duplication
    #  Improves UI security
    #  Django standard approach
    #  Template safe
    #  Reusable logic
    # --------------------------------------------------------
    return {'available_roles': roles}


def user_credits(request):
    # --------------------------------------------------------
    # Handle unauthenticated users
    #  Anonymous users have no wallet
    #  Prevents DB queries
    #  Avoids crashes
    #  Safe default value
    #  Improves security
    #  Template-friendly
    #  Performance optimized
    #  Clean exit
    # --------------------------------------------------------
    if not request.user.is_authenticated:
        return {'available_credits': 0}

    # --------------------------------------------------------
    # Fetch latest wallet entry
    #  Wallet is versioned/history-based
    #  Latest entry holds correct balance
    #  Ordered by wallet_id
    #  First() prevents exceptions
    #  Efficient query
    #  Safe access
    #  Supports audit trail
    #  Business logic compliant
    # --------------------------------------------------------
    wallet_obj = Wallet.objects.filter(user=request.user).order_by('-wallet_id').first()

    # --------------------------------------------------------
    # Extract balance safely
    #  If wallet exists → use balance
    #  If not → default to zero
    #  Prevents NoneType error
    #  Ensures numeric output
    #  Template-safe
    #  Business rule compliant
    #  Easy to extend
    #  Clean logic
    # --------------------------------------------------------
    available_credits = wallet_obj.balance if wallet_obj else 0

    # --------------------------------------------------------
    # Return credit balance
    #  Used in header/UI
    #  Available in all templates
    #  Avoids passing from views
    #  Centralized wallet logic
    #  Cleaner templates
    #  Reusable context
    #  Secure data access
    #  Django best practice
    # --------------------------------------------------------
    return {'available_credits': available_credits}


def role_permissions(request):
    """
    Permission matrix:
    Feature               | SuperUser | SuperAdmin | SupportAdmin | Reseller | Client
    Dashboard             |     Y     |     Y      |      Y       |    Y     |   Y
    Create Company        |     Y     |     N      |      N       |    N     |   N
    Create Role           |     Y     |     N      |      N       |    N     |   N
    User Management       |     Y     |     Y      |      Y       |    Y     |   N
    Credit Management     |     Y     |     Y      |      Y       |    Y     |   N
    Sender                |     Y     |     Y      |      Y       |    Y     |   Y
    Message Template      |     Y     |     Y      |      Y       |    Y     |   Y
    Group List            |     Y     |     Y      |      N       |    N     |   Y
    Compose Message       |     Y     |     Y      |      N       |    N     |   Y
    SMS Summary           |     Y     |     Y      |      Y       |    Y     |   Y
    DLR Report            |     Y     |     Y      |      Y       |    Y     |   Y
    SMPP Connections      |     Y     |     Y      |      N       |    N     |   N
    Routes                |     Y     |     Y      |      Y       |    Y     |   N
    """
    permissions = {
        'can_access_dashboard':       True,
        'can_create_company':         False,
        'can_create_role':            False,
        'can_manage_users':           False,
        'can_manage_credits':         False,
        'can_manage_wallet':          False,
        'can_view_transaction_report':False,
        'can_manage_senders':         False,
        'can_manage_templates':       False,
        'can_manage_groups':          False,
        'can_compose_message':        False,
        'can_view_sms_summary':       False,
        'can_view_dlr':               False,
        'can_manage_smpp':            False,
        'can_manage_routes':          False,
        'can_manage_routing_mapping': False,
        'user_role':                  None,
    }

    if not request.user.is_authenticated:
        return {'permissions': permissions}

    user = request.user

    if user.is_superuser:
        for key in permissions:
            if key.startswith('can_'):
                permissions[key] = True
        permissions['user_role'] = 'Super User'
        return {'permissions': permissions}

    user_role = None
    if hasattr(user, 'role') and user.role:
        user_role = user.role.role_name if hasattr(user.role, 'role_name') else str(user.role)

    permissions['user_role'] = user_role

    if user_role == 'Super Admin':
        permissions.update({
            'can_create_company':          False,
            'can_create_role':             False,
            'can_manage_users':            True,
            'can_manage_credits':          True,
            'can_manage_wallet':           True,
            'can_view_transaction_report': True,
            'can_manage_senders':          True,
            'can_manage_templates':        True,
            'can_manage_groups':           True,
            'can_compose_message':         True,
            'can_view_sms_summary':        True,
            'can_view_dlr':                True,
            'can_manage_smpp':             True,
            'can_manage_routes':           True,
            'can_manage_routing_mapping':  True,
        })

    elif user_role == 'Support Admin':
        permissions.update({
            'can_create_company':          False,
            'can_create_role':             False,
            'can_manage_users':            True,
            'can_manage_credits':          True,
            'can_manage_wallet':           True,
            'can_view_transaction_report': True,
            'can_manage_senders':          True,
            'can_manage_templates':        True,
            'can_manage_groups':           False,
            'can_compose_message':         False,
            'can_view_sms_summary':        True,
            'can_view_dlr':                True,
            'can_manage_smpp':             False,
            'can_manage_routes':           True,
            'can_manage_routing_mapping':  True,
        })

    elif user_role == 'Reseller':
        permissions.update({
            'can_create_company':          False,
            'can_create_role':             False,
            'can_manage_users':            True,
            'can_manage_credits':          True,
            'can_manage_wallet':           True,
            'can_view_transaction_report': True,
            'can_manage_senders':          True,
            'can_manage_templates':        True,
            'can_manage_groups':           False,
            'can_compose_message':         False,
            'can_view_sms_summary':        True,
            'can_view_dlr':                True,
            'can_manage_smpp':             False,
            'can_manage_routes':           True,
            'can_manage_routing_mapping':  True,
        })

    elif user_role == 'Client':
        permissions.update({
            'can_create_company':          False,
            'can_create_role':             False,
            'can_manage_users':            False,
            'can_manage_credits':          False,
            'can_manage_wallet':           False,
            'can_view_transaction_report': False,
            'can_manage_senders':          True,
            'can_manage_templates':        True,
            'can_manage_groups':           True,
            'can_compose_message':         True,
            'can_view_sms_summary':        True,
            'can_view_dlr':                True,
            'can_manage_smpp':             False,
            'can_manage_routes':           False,
            'can_manage_routing_mapping':  False,
        })

    return {'permissions': permissions}
