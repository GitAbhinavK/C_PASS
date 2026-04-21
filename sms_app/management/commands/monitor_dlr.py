# management/commands/monitor_dlr.py

from django.core.management.base import BaseCommand
from sms_app.smpp_dlr_manager import get_dlr_status, get_all_dlr_listeners
import time

class Command(BaseCommand):
    help = 'Monitor DLR listeners status'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--continuous',
            action='store_true',
            help='Run in continuous monitoring mode',
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=10,
            help='Interval between checks in seconds (default: 10)',
        )
    
    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 Starting DLR Monitor...'))
        self.stdout.write(f"📊 Interval: {options['interval']} seconds")
        self.stdout.write("=" * 60)
        
        if options['continuous']:
            self.stdout.write("🔄 Running in continuous mode (Ctrl+C to stop)")
            self.stdout.write("=" * 60)
        
        try:
            while True:
                self._print_status()
                
                if not options['continuous']:
                    break
                
                time.sleep(options['interval'])
                
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\n⏹️ Stopped by user"))
    
    def _print_status(self):
        """Print current DLR listener status"""
        try:
            # Get status
            status_list = get_dlr_status()
            
            if not status_list:
                self.stdout.write(self.style.WARNING('ℹ️ No active DLR listeners found'))
                self.stdout.write("=" * 60)
                return
            
            self.stdout.write(f"📡 Found {len(status_list)} active DLR listener(s):")
            self.stdout.write("=" * 60)
            
            for status in status_list:
                if status['running']:
                    status_text = self.style.SUCCESS('RUNNING')
                else:
                    status_text = self.style.ERROR('STOPPED')
                
                self.stdout.write(
                    f"SMPP ID: {status['smpp_id']} | "
                    f"Status: {status_text} | "
                    f"Messages: {self.style.SUCCESS(str(status['messages_processed']))} | "
                    f"Last Activity: {status['last_activity']}"
                )
            
            self.stdout.write("=" * 60)
            
            # Also check recent messages in database
            self._check_recent_messages()
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error getting status: {str(e)}"))
    
    def _check_recent_messages(self):
        """Check recent messages in database"""
        from sms_app.models import ComposeMessageLine
        from django.utils import timezone
        from datetime import timedelta
        
        try:
            # Get messages from last 10 minutes
            recent_messages = ComposeMessageLine.objects.filter(
                submit_time__gte=timezone.now() - timedelta(minutes=10)
            ).order_by('-submit_time')[:5]
            
            if recent_messages:
                self.stdout.write("\n📋 Recent Messages (last 10 minutes):")
                self.stdout.write("-" * 60)
                
                for msg in recent_messages:
                    if msg.status == "DELIVERED":
                        status_text = self.style.SUCCESS(f"✓ {msg.status}")
                    elif msg.status == "FAILED":
                        status_text = self.style.ERROR(f"✗ {msg.status}")
                    else:
                        status_text = f"⏳ {msg.status}"
                    
                    self.stdout.write(
                        f"ID: {msg.message_id[:8]}... | "
                        f"To: {msg.mobile_number} | "
                        f"Status: {status_text} | "
                        f"Time: {msg.submit_time.strftime('%H:%M:%S')}"
                    )
                
                self.stdout.write("-" * 60)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"⚠️ Could not check recent messages: {str(e)}"))