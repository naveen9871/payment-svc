from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import logging
import random

from .models import Payment, IdempotencyKey
from .serializers import (
    PaymentSerializer, 
    ChargePaymentSerializer, 
    RefundPaymentSerializer,
    PaymentListSerializer
)
from .events import publish_payment_event

logger = logging.getLogger(__name__)


class PaymentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


@api_view(['POST'])
def charge_payment(request):
    """
    POST /v1/payments/charge
    Process a payment with idempotency support
    """
    serializer = ChargePaymentSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response({
            'error': 'validation_error',
            'details': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    idempotency_key = data['idempotency_key']
    
    try:
        # Check if idempotency key exists
        with transaction.atomic():
            idem_key, created = IdempotencyKey.objects.get_or_create(
                key=idempotency_key,
                defaults={
                    'request_data': request.data,
                    'expires_at': timezone.now() + timedelta(hours=24)
                }
            )
            
            if not created:
                # Idempotency key already exists
                if idem_key.status == 'COMPLETED' and idem_key.payment:
                    # Return existing payment
                    payment_serializer = PaymentSerializer(idem_key.payment)
                    return Response({
                        'message': 'Payment already processed (idempotent)',
                        'payment': payment_serializer.data
                    }, status=status.HTTP_200_OK)
                elif idem_key.status == 'PROCESSING':
                    return Response({
                        'error': 'duplicate_request',
                        'message': 'Payment is being processed'
                    }, status=status.HTTP_409_CONFLICT)
            
            # Create payment
            payment = Payment.objects.create(
                order_id=data['order_id'],
                amount=data['amount'],
                method=data['method'],
                status='PENDING'
            )
            
            # Update idempotency key
            idem_key.payment = payment
            idem_key.save()
            
            # Simulate payment processing
            payment_result = process_payment_gateway(payment, data)
            
            if payment_result['success']:
                payment.status = 'SUCCESS'
                payment.gateway_response = payment_result
                payment.save()
                
                # Update idempotency key status
                idem_key.status = 'COMPLETED'
                idem_key.response_data = PaymentSerializer(payment).data
                idem_key.save()
                
                # Publish success event
                publish_payment_event('payment.succeeded', {
                    'payment_id': payment.payment_id,
                    'order_id': payment.order_id,
                    'amount': float(payment.amount),
                    'method': payment.method,
                    'reference': payment.reference
                })
                
                logger.info(f"Payment {payment.payment_id} succeeded for order {payment.order_id}")
                
                return Response({
                    'message': 'Payment successful',
                    'payment': PaymentSerializer(payment).data
                }, status=status.HTTP_201_CREATED)
            else:
                payment.status = 'FAILED'
                payment.failure_reason = payment_result.get('reason', 'Payment declined')
                payment.gateway_response = payment_result
                payment.save()
                
                # Update idempotency key
                idem_key.status = 'FAILED'
                idem_key.response_data = {'error': payment_result.get('reason')}
                idem_key.save()
                
                # Publish failure event
                publish_payment_event('payment.failed', {
                    'payment_id': payment.payment_id,
                    'order_id': payment.order_id,
                    'amount': float(payment.amount),
                    'reason': payment.failure_reason
                })
                
                logger.warning(f"Payment {payment.payment_id} failed for order {payment.order_id}")
                
                return Response({
                    'error': 'payment_failed',
                    'message': payment.failure_reason,
                    'payment': PaymentSerializer(payment).data
                }, status=status.HTTP_402_PAYMENT_REQUIRED)
                
    except Exception as e:
        logger.error(f"Error processing payment: {str(e)}")
        return Response({
            'error': 'processing_error',
            'message': 'Failed to process payment'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def refund_payment(request, payment_id):
    """
    POST /v1/payments/{payment_id}/refund
    Refund a payment (full or partial)
    """
    serializer = RefundPaymentSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response({
            'error': 'validation_error',
            'details': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    idempotency_key = data['idempotency_key']
    
    try:
        payment = Payment.objects.get(payment_id=payment_id)
    except Payment.DoesNotExist:
        return Response({
            'error': 'not_found',
            'message': 'Payment not found'
        }, status=status.HTTP_404_NOT_FOUND)
    
    if payment.status != 'SUCCESS':
        return Response({
            'error': 'invalid_status',
            'message': 'Only successful payments can be refunded'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        with transaction.atomic():
            # Check idempotency
            refund_key = f"refund_{payment_id}_{idempotency_key}"
            idem_key, created = IdempotencyKey.objects.get_or_create(
                key=refund_key,
                defaults={
                    'request_data': request.data,
                    'payment': payment,
                    'expires_at': timezone.now() + timedelta(hours=24)
                }
            )
            
            if not created and idem_key.status == 'COMPLETED':
                return Response({
                    'message': 'Refund already processed (idempotent)',
                    'payment': PaymentSerializer(payment).data
                }, status=status.HTTP_200_OK)
            
            # Determine refund amount
            refund_amount = data.get('amount', payment.amount - payment.refunded_amount)
            
            # Validate refund amount
            remaining_amount = payment.amount - payment.refunded_amount
            if refund_amount > remaining_amount:
                return Response({
                    'error': 'invalid_amount',
                    'message': f'Refund amount exceeds available amount ({remaining_amount})'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Process refund
            payment.refunded_amount += refund_amount
            
            if payment.refunded_amount >= payment.amount:
                payment.status = 'REFUNDED'
            else:
                payment.status = 'PARTIAL_REFUND'
            
            payment.save()
            
            # Update idempotency key
            idem_key.status = 'COMPLETED'
            idem_key.response_data = PaymentSerializer(payment).data
            idem_key.save()
            
            # Publish refund event
            publish_payment_event('payment.refunded', {
                'payment_id': payment.payment_id,
                'order_id': payment.order_id,
                'refund_amount': float(refund_amount),
                'total_refunded': float(payment.refunded_amount),
                'reason': data.get('reason', 'Customer request')
            })
            
            logger.info(f"Payment {payment.payment_id} refunded: {refund_amount}")
            
            return Response({
                'message': 'Refund processed successfully',
                'refund_amount': refund_amount,
                'payment': PaymentSerializer(payment).data
            }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error processing refund: {str(e)}")
        return Response({
            'error': 'processing_error',
            'message': 'Failed to process refund'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def get_payment(request, payment_id):
    """
    GET /v1/payments/{payment_id}
    Retrieve payment details
    """
    try:
        payment = Payment.objects.get(payment_id=payment_id)
        serializer = PaymentSerializer(payment)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except Payment.DoesNotExist:
        return Response({
            'error': 'not_found',
            'message': 'Payment not found'
        }, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
def list_payments(request):
    """
    GET /v1/payments
    List all payments with pagination and filters
    """
    queryset = Payment.objects.all()
    
    # Apply filters
    order_id = request.query_params.get('order_id')
    status_filter = request.query_params.get('status')
    method = request.query_params.get('method')
    
    if order_id:
        queryset = queryset.filter(order_id=order_id)
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if method:
        queryset = queryset.filter(method=method)
    
    # Pagination
    paginator = PaymentPagination()
    page = paginator.paginate_queryset(queryset, request)
    
    if page is not None:
        serializer = PaymentListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
    
    serializer = PaymentListSerializer(queryset, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
def health_check(request):
    """Health check endpoint"""
    return Response({
        'status': 'healthy',
        'service': 'payment-service',
        'version': 'v1.0.0'
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
def ready_check(request):
    """Readiness check endpoint"""
    from django.db import connection
    try:
        connection.ensure_connection()
        return Response({
            'status': 'ready',
            'database': 'connected'
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({
            'status': 'not_ready',
            'error': str(e)
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def process_payment_gateway(payment, data):
    """
    Simulate payment gateway processing
    In production, integrate with real payment gateway (Razorpay, Stripe, etc.)
    """
    # Simulate 90% success rate
    success = random.random() < 0.9
    
    if success:
        return {
            'success': True,
            'transaction_id': f"TXN{random.randint(100000, 999999)}",
            'gateway': 'SimulatedGateway',
            'timestamp': timezone.now().isoformat()
        }
    else:
        reasons = [
            'Insufficient funds',
            'Card declined',
            'Invalid card details',
            'Payment timeout'
        ]
        return {
            'success': False,
            'reason': random.choice(reasons),
            'gateway': 'SimulatedGateway'
        }
        
from django.http import JsonResponse

def health_check(request):
    return JsonResponse({"status": "ok"})

def ready_check(request):
    return JsonResponse({"status": "ready"})
