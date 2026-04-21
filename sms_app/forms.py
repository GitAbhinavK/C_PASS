
# sms_app/forms.py
# sms_app/forms.py
from django import forms
from django.forms import inlineformset_factory
from .models import ComposeMessage, ComposeMessageLine
from sms_app.models import User
from sms_app.models import Sender
from sms_app.models import MessageTemplate  # update app names if needed

MESSAGE_TYPE_CHOICES = [
    ('TR', 'Transactional'),
    ('TX', 'Promotional'),
    ('TRX', 'Mixed'),
]

class ComposeMessageForm(forms.ModelForm):
    # dropdowns
    user_id = forms.ModelChoiceField(
        queryset=User.objects.all(),
        label="User Name",
        widget=forms.Select(attrs={'class': 'form-control'}),
        required=False
    )
    sender_id = forms.ModelChoiceField(
        queryset=Sender.objects.all(),
        label="Sender Name",
        widget=forms.Select(attrs={'class': 'form-control'}),
        required=False
    )
    template_id = forms.ModelChoiceField(
        queryset=MessageTemplate.objects.all(),
        label="Template Name",
        widget=forms.Select(attrs={'class': 'form-control'}),
        required=False
    )

    message_type = forms.ChoiceField(
        choices=MESSAGE_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    class Meta:
        model = ComposeMessage
        fields = [
            'user_id', 'sender_id', 'template_id', 'send_type', 'message_type',
            'smart_link_flag', 'remove_duplications_flag', 'send_as_flag_messsage_flag',
            'schedule', 'whatsapp_url', 'start_date', 'end_date'
        ]
        widgets = {
            'send_type': forms.TextInput(attrs={'class': 'form-control'}),
            'schedule': forms.TextInput(attrs={'class': 'form-control'}),
            'whatsapp_url': forms.URLInput(attrs={'class': 'form-control'}),
            'start_date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'end_date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
        }

class ComposeMessageLineForm(forms.ModelForm):
    class Meta:
        model = ComposeMessageLine
        fields = ['mobile_number', 'text_mes']
        widgets = {
            'mobile_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter Mobile Number'}),
            'text_mes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Enter Message Text'}),
        }

ComposeMessageLineFormSet = inlineformset_factory(
    ComposeMessage,
    ComposeMessageLine,
    form=ComposeMessageLineForm,
    extra=1,
    can_delete=True
)
