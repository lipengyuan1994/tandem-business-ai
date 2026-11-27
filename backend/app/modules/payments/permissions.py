CONNECTION_READ = "payments.connection.read"
CONNECTION_MANAGE = "payments.connection.manage"
LINK_SHARE = "payments.link.share"
# Asking for a card refund puts it in the approvals queue; only an owner or admin can approve it (M21 rule 1).
REFUND_PROPOSE = "payments.refund.propose"
REFUND_SEND = "payments.refund.send"
ALL = frozenset({CONNECTION_READ, CONNECTION_MANAGE, LINK_SHARE, REFUND_PROPOSE, REFUND_SEND})
