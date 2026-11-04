DOCUMENT_READ = "sales.document.read"
DOCUMENT_WRITE = "sales.document.write"
DOCUMENT_ISSUE = "sales.document.issue"
MARK_PAID = "sales.invoice.mark_paid"
CREDIT = "sales.invoice.credit"
PROPOSAL_APPROVE = "sales.proposal.approve"
ALL = frozenset({DOCUMENT_READ, DOCUMENT_WRITE, DOCUMENT_ISSUE, MARK_PAID, CREDIT, PROPOSAL_APPROVE})
