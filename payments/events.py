import pika
import json
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def get_rabbitmq_connection():
    """Create RabbitMQ connection"""
    try:
        credentials = pika.PlainCredentials(
            settings.RABBITMQ_USER,
            settings.RABBITMQ_PASSWORD
        )
        parameters = pika.ConnectionParameters(
            host=settings.RABBITMQ_HOST,
            port=settings.RABBITMQ_PORT,
            credentials=credentials,
            heartbeat=600,
            blocked_connection_timeout=300
        )
        return pika.BlockingConnection(parameters)
    except Exception as e:
        logger.error(f"Failed to connect to RabbitMQ: {str(e)}")
        return None


def publish_payment_event(event_type, data):
    """
    Publish payment events to RabbitMQ
    
    Events:
    - payment.succeeded
    - payment.failed
    - payment.refunded
    """
    connection = None
    try:
        connection = get_rabbitmq_connection()
        if not connection:
            logger.error("Cannot publish event - RabbitMQ connection failed")
            return False
        
        channel = connection.channel()
        
        # Declare exchange
        channel.exchange_declare(
            exchange='payment_events',
            exchange_type='topic',
            durable=True
        )
        
        # Prepare message
        message = {
            'event_type': event_type,
            'timestamp': str(data.get('timestamp', '')),
            'data': data
        }
        
        # Publish message
        channel.basic_publish(
            exchange='payment_events',
            routing_key=event_type,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Persistent message
                content_type='application/json'
            )
        )
        
        logger.info(f"Published event: {event_type} for payment {data.get('payment_id')}")
        return True
        
    except Exception as e:
        logger.error(f"Error publishing payment event: {str(e)}")
        return False
    finally:
        if connection and not connection.is_closed:
            connection.close()


def consume_order_events():
    """
    Consume order cancellation events for automatic refunds
    """
    connection = None
    try:
        connection = get_rabbitmq_connection()
        if not connection:
            logger.error("Cannot consume events - RabbitMQ connection failed")
            return
        
        channel = connection.channel()
        
        # Declare exchange
        channel.exchange_declare(
            exchange='order_events',
            exchange_type='topic',
            durable=True
        )
        
        # Declare queue
        queue_name = 'payment_service_order_events'
        channel.queue_declare(queue=queue_name, durable=True)
        
        # Bind to order.cancelled events
        channel.queue_bind(
            exchange='order_events',
            queue=queue_name,
            routing_key='order.cancelled'
        )
        
        def callback(ch, method, properties, body):
            try:
                message = json.loads(body)
                event_type = message.get('event_type')
                data = message.get('data', {})
                
                if event_type == 'order.cancelled':
                    handle_order_cancellation(data)
                
                ch.basic_ack(delivery_tag=method.delivery_tag)
                
            except Exception as e:
                logger.error(f"Error processing order event: {str(e)}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(queue=queue_name, on_message_callback=callback)
        
        logger.info("Started consuming order events...")
        channel.start_consuming()
        
    except Exception as e:
        logger.error(f"Error consuming order events: {str(e)}")
    finally:
        if connection and not connection.is_closed:
            connection.close()


def handle_order_cancellation(data):
    """
    Handle order cancellation event by triggering refund
    """
    from .models import Payment
    from django.db import transaction
    
    order_id = data.get('order_id')
    if not order_id:
        logger.error("Order ID not found in cancellation event")
        return
    
    try:
        with transaction.atomic():
            # Find successful payments for this order
            payments = Payment.objects.filter(
                order_id=order_id,
                status='SUCCESS'
            )
            
            for payment in payments:
                if payment.refunded_amount < payment.amount:
                    # Calculate remaining refundable amount
                    refund_amount = payment.amount - payment.refunded_amount
                    
                    # Process refund
                    payment.refunded_amount += refund_amount
                    payment.status = 'REFUNDED'
                    payment.save()
                    
                    # Publish refund event
                    publish_payment_event('payment.refunded', {
                        'payment_id': payment.payment_id,
                        'order_id': payment.order_id,
                        'refund_amount': float(refund_amount),
                        'reason': 'Order cancellation'
                    })
                    
                    logger.info(f"Auto-refunded payment {payment.payment_id} due to order cancellation")
                    
    except Exception as e:
        logger.error(f"Error handling order cancellation: {str(e)}")