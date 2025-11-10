from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
import uuid

class Payment(models.Model):
    """Payment model for handling payment transactions"""
    
    PAYMENT_METHODS = [
        ('CARD', 'Credit/Debit Card'),
        ('UPI', 'UPI'),
        ('COD', 'Cash on Delivery'),
        ('NET_BANKING', 'Net Banking'),
    ]
    
    PAYMENT_STATUS = [
        ('PENDING', 'Pending'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
        ('PARTIAL_REFUND', 'Partially Refunded'),
    ]
    
    payment_id = models.AutoField(primary_key=True)
    order_id = models.IntegerField(db_index=True)
    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS, default='PENDING')
    reference = models.CharField(max_length=100, unique=True)
    refunded_amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=Decimal('0.00')
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Additional fields for audit
    gateway_response = models.JSONField(null=True, blank=True)
    failure_reason = models.TextField(null=True, blank=True)
    
    class Meta:
        db_table = 'payments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['order_id', 'status']),
            models.Index(fields=['reference']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Payment {self.payment_id} - Order {self.order_id} - {self.status}"
    
    def generate_reference(self):
        """Generate unique payment reference"""
        from datetime import datetime
        date_str = datetime.now().strftime('%Y%m%d')
        unique_id = str(uuid.uuid4())[:6].upper()
        return f"ECI{date_str}-{unique_id}"
    
    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self.generate_reference()
        super().save(*args, **kwargs)


class IdempotencyKey(models.Model):
    """Model to track idempotency keys for preventing duplicate charges"""
    
    key = models.CharField(max_length=255, unique=True, primary_key=True)
    payment = models.ForeignKey(
        Payment, 
        on_delete=models.CASCADE, 
        related_name='idempotency_keys',
        null=True,
        blank=True
    )
    request_data = models.JSONField()
    response_data = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=20, default='PROCESSING')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    
    class Meta:
        db_table = 'idempotency_keys'
        indexes = [
            models.Index(fields=['expires_at']),
        ]
    
    def __str__(self):
        return f"IdempotencyKey: {self.key}"
    
    @classmethod
    def is_expired(cls, key):
        """Check if idempotency key is expired"""
        from django.utils import timezone
        try:
            idem_key = cls.objects.get(key=key)
            return idem_key.expires_at < timezone.now()
        except cls.DoesNotExist:
            return False