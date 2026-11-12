from django.urls import path

from .api import CreditRequestPayView, CreditRequestView, PricingView, TransactionListView, WalletView

urlpatterns = [
	path("wallet/", WalletView.as_view(), name="billing-wallet"),
	path("transactions/", TransactionListView.as_view(), name="billing-transactions"),
	path("pricing/", PricingView.as_view(), name="billing-pricing"),
	path("credit-requests/", CreditRequestView.as_view(), name="billing-credit-requests"),
	path("credit-requests/<int:pk>/payment/", CreditRequestPayView.as_view(), name="billing-credit-request-pay"),
]
