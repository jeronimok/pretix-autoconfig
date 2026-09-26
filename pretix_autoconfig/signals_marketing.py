# Marketing opt-in is handled as a standard Pretix question (identifier
# autoconfig_marketing_optin) on the checkout questions step. No signal
# interception required — the platform reads the question answer from
# order.positions[].answers in its order sync.
