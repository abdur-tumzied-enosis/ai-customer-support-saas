from django.urls import path

from .api import PricingView, TransactionListView, WalletView

urlpatterns = [
	path("wallet/", WalletView.as_view(), name="billing-wallet"),
	path("transactions/", TransactionListView.as_view(), name="billing-transactions"),
	path("pricing/", PricingView.as_view(), name="billing-pricing"),
]
