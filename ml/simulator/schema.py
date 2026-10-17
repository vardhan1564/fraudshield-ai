"""Names shared by the simulator and its tests: scenarios and the columns of each output file."""

# Injected fraud scenarios with a share of transaction rows (config.yaml `shares`).
SHARED_SCENARIOS = (
    "digital_arrest",
    "fake_support_refund",
    "investment_task",
    "account_takeover",
    "card_testing",
)
# All values of transactions.scenario (null = legit).
SCENARIOS = (*SHARED_SCENARIOS, "mule_ring")

# Legit rows that look risky (transactions.hard_negative).
HARD_NEGATIVES = ("big_legit", "new_payee_legit", "night_legit", "call_legit")

CHANNELS = ("UPI", "CARD", "WALLET")
REMOTE_ACCESS_APPS = ("AnyDesk", "TeamViewer", "QuickSupport")

USER_COLUMNS = [
    "user_id",
    "name",
    "phone",
    "age_band",
    "city",
    "city_tier",
    "home_lat",
    "home_lon",
    "avg_txn_amount",
    "daily_rate",
    "active_hour_start",
    "active_hour_end",
    "kyc_level",
    "vpa",
    "account_created_ts",
    "sim_changed_at",
    "is_mule",
    "ring_id",
]

DEVICE_COLUMNS = [
    "device_id",
    "user_id",
    "fingerprint",
    "os",
    "is_rooted",
    "first_seen",
    "last_seen",
]

TRANSACTION_COLUMNS = [
    # payment (spec 04 §2.1 request, spec 05 transactions)
    "txn_id",
    "user_id",
    "payee_vpa",
    "payee_user_id",
    "payee_account_age_days",
    "amount",
    "currency",
    "channel",
    "merchant_category",
    "device_id",
    "ip",
    "is_vpn",
    "geo_lat",
    "geo_lon",
    "ts",
    "status",
    # context (spec 01 F2)
    "on_call",
    "caller_in_contacts",
    "call_duration_s",
    "screen_share_active",
    "remote_access_app",
    # behaviour SDK
    "typing_speed_z",
    "session_len_s",
    "login_to_pay_s",
    # ground truth (never a model feature)
    "scenario",
    "is_fraud",
    "hard_negative",
    "ring_id",
]

# Same shape as the graph_edges table (spec 05).
EDGE_COLUMNS = ["edge_type", "src_id", "dst_id", "amount", "txn_id", "ts"]

REPORT_COLUMNS = ["report_id", "reporter_id", "payee_vpa", "scam_type", "ts"]

OUTPUT_COLUMNS = {
    "transactions": TRANSACTION_COLUMNS,
    "users": USER_COLUMNS,
    "devices": DEVICE_COLUMNS,
    "edges": EDGE_COLUMNS,
    "reports": REPORT_COLUMNS,
}
