# Acceptance walk, spec section 9

Test paths are under backend/. Status: COVERED, PARTIAL, NOT COVERED, MANUAL.

| Criterion | Status | Test / gap |
|---|---|---|
| Each project has own wallet; user in two projects sees two balances; membership changes don't touch it | COVERED | billing/tests/test_api.py WalletTest.test_two_projects_two_balances_membership_untouched (added) |
| Every balance change has an immutable ledger row; retried key creates no second row | COVERED | test_service.py ModelTests.test_transaction_immutable, ServiceTests.test_charge_same_key_once, test_grant_and_balance_matches_ledger |
| Two simultaneous charges leave balance = sum of ledger | COVERED | test_service.py ConcurrencyTests.test_concurrent_charges_consistent |
| Changing ActionPrice doesn't change existing ledger rows | COVERED | test_service.py ServiceTests.test_price_snapshot |
| Deleting an account keeps wallet (project null, project_name kept) and every ledger row | COVERED | test_allocation.py AllocationTests.test_purge_project_keeps_ledger (wallet null/name asserted; row survives via refresh_from_db) |
| Reconciler reports altered wallet, doesn't change it | COVERED | test_reconcile.py ReconcileTest.test_check_balances_reports_mismatch |
| Every existing project gets exactly 100 once; re-run grants nothing | COVERED | test_allocation.py test_initial_allocation_once |
| First created project gets signup bonus, also if invited elsewhere first | COVERED | test_allocation.py test_invited_member_still_gets_bonus, test_verify_grants_signup_bonus |
| Two simultaneous project creations grant one bonus | COVERED | test_allocation.py ConcurrentSignupBonusTest.test_simultaneous_claims_grant_one_bonus (added, 4 threads). Note: it calls grant_signup_bonus concurrently, not the signup endpoint |
| Second created project and recreated project get none | PARTIAL | test_second_created_project_gets_no_bonus covers second; recreated is by per-user key (same mechanism, not separately asserted) |
| Delivered reply charged once, via process_reply or redeliver | COVERED | test_reply_billing.py test_delivered_reply_charged_once, test_failed_then_redelivered_charged_once |
| Crash after delivery before charge: reconciler charges once | COVERED | test_reconcile.py test_reconcile_charges_delivered_uncharged (runs reconcile twice, one row) |
| Never-delivered reply, fallback, handoff, test-chat not charged | COVERED | test_reply_billing.py test_never_delivered_not_charged, test_fallback_reply_not_charged_even_after_redeliver, test_handoff_reply_not_charged, test_test_chat_free_and_never_blocked |
| Hidden work adds to conversation USD, never credits | COVERED | test_report.py ReportTest.test_hidden_work_adds_usd_not_credits (added) |
| Photo search charged even if reply times out | COVERED | test_reply_billing.py test_photo_search_charged_even_if_reply_times_out |
| Missing ActionPrice charges default and logs error | COVERED | test_service.py test_missing_price_row_uses_default |
| Image/embed usage events cost_usd = 0, not counted as unpriced | COVERED | usage/tests/test_image_usage.py (cost_usd == 0 for image; embed None -> 0, chat stays None) |
| Shopper turn past -overdraft (reply + photo) handed off "out of credits" | COVERED | test_reply_billing.py test_out_of_credits_hands_off, test_overdraft_counts_photo; test_service.py test_shopper_blocked |
| Owner action balance < price returns 402 and runs nothing; passed check charged after success even if balance dropped | COVERED | test_owner_actions.py test_setup_run_402_when_broke, test_improve_charged_on_success_only, test_owner_action_after_check_can_go_negative |
| Catalog extraction keeps affordable only, never below zero (also concurrent drop), reports found/saved/skipped and stopped | COVERED | test_catalog_billing.py test_partial_page_keeps_affordable, test_concurrent_drop_during_page |
| Product import at price 0 keeps all, no ledger rows | COVERED | test_catalog_billing.py test_zero_price_keeps_all_candidates |
| Concurrent depleting charges send one email; grant re-arms; no admins logs warning | COVERED | test_service.py ConcurrencyTests.test_one_depletion_email_under_concurrency, test_depletion_email_once_and_rearm, test_no_admins_logs_warning |
| Prices change in admin without deploy and show in admin history | MANUAL | Check ActionPrice change in /admin and its History tab |
| Grants/adjustments carry created_by and a note | COVERED | test_report.py AdminTest.test_add_transaction_goes_through_grant |
| Members see balance and per-conversation credits; only admins see ledger page | PARTIAL | API side COVERED: test_api.py WalletTest, TransactionsTest.test_member_forbidden_stranger_404, ConversationCreditsTest. Ledger page visibility in frontend is MANUAL |
| Report flags unpriced usage and never calls estimate revenue | COVERED | test_report.py test_coverage_sums_and_flags (unpriced count); AdminTest.test_coverage_page_flags_unpriced_and_says_estimate (added: "incomplete", "estimate", no "revenue") |
| No customer endpoint exposes USD or internal settings | COVERED | test_api.py WalletTest/TransactionsTest/PricingTest no-leak checks |
| "Free credits" hidden when signup_credits is 0 (UI) | MANUAL | Frontend; API returns signup_credits, UI hiding unchecked |

Counts: covered 22, partial 2, not covered 0, manual 2 (26 rows; "Members see..." counted partial).
