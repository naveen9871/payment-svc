from django.urls import path
from . import views

urlpatterns = [
    # Payment endpoints
    path('v1/payments/charge', views.charge_payment, name='charge_payment'),
    path('v1/payments/<int:payment_id>/refund', views.refund_payment, name='refund_payment'),
    path('v1/payments/<int:payment_id>', views.get_payment, name='get_payment'),
    path('v1/payments', views.list_payments, name='list_payments'),

    # Health checks
    path('health', views.health_check, name='health_check'),
    path('health/ready', views.ready_check, name='ready_check'),
]
