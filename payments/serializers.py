from rest_framework import serializers
from .models import Payment, IdempotencyKey
from decimal import Decimal

class PaymentSerializer(serializers.ModelSerializer):
    """Serializer for Payment model"""
    
    class Meta:
        model = Payment
        fields = [
            'payment_id', 'order_id', 'amount', 'method', 'status',
            'reference', 'refunded_amount', 'created_at', 'updated_at',
            'failure_reason'
        ]
        read_only_fields = ['payment_id', 'reference', 'created_at', 'updated_at', 'status']


class ChargePaymentSerializer(serializers.Serializer):
    """Serializer for charging a payment"""
    
    order_id = serializers.IntegerField(min_value=1)
    amount = serializers.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        min_value=Decimal('0.01')
    )
    method = serializers.ChoiceField(choices=Payment.PAYMENT_METHODS)
    idempotency_key = serializers.CharField(max_length=255, required=True)
    customer_info = serializers.JSONField(required=False, default=dict)
    
    def validate_amount(self, value):
        """Validate amount is positive"""
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than 0")
        return value
    
    def validate_idempotency_key(self, value):
        """Validate idempotency key format"""
        if not value or len(value) < 10:
            raise serializers.ValidationError(
                "Idempotency key must be at least 10 characters"
            )
        return value


class RefundPaymentSerializer(serializers.Serializer):
    """Serializer for refunding a payment"""
    
    amount = serializers.DecimalField(
        max_digits=10, 
        decimal_places=2,
        required=False
    )
    reason = serializers.CharField(max_length=500, required=False)
    idempotency_key = serializers.CharField(max_length=255, required=True)
    
    def validate_amount(self, value):
        """Validate refund amount"""
        if value is not None and value <= 0:
            raise serializers.ValidationError("Refund amount must be greater than 0")
        return value


class PaymentListSerializer(serializers.ModelSerializer):
    """Serializer for listing payments"""
    
    class Meta:
        model = Payment
        fields = [
            'payment_id', 'order_id', 'amount', 'method', 
            'status', 'reference', 'created_at'
        ]