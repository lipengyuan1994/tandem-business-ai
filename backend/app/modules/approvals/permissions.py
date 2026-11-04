PROPOSAL_READ = "approvals.proposal.read"
PROPOSAL_CREATE = "approvals.proposal.create"
ASSISTANT_USE = "ai.assistant.use"  # assistant-originated proposals (ai module, EVENT phase)
ALL = frozenset({PROPOSAL_READ, PROPOSAL_CREATE})
