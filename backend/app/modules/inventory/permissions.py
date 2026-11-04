STOCK_READ = "inventory.stock.read"
STOCK_ADJUST = "inventory.stock.adjust"
REORDER_READ = "inventory.reorder.read"
REORDER_WRITE = "inventory.reorder.write"
PROPOSAL_APPROVE = "inventory.proposal.approve"
ALL = frozenset({STOCK_READ, STOCK_ADJUST, REORDER_READ, REORDER_WRITE, PROPOSAL_APPROVE})
