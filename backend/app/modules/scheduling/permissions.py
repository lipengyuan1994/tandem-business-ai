RESOURCE_READ = "scheduling.resource.read"
RESOURCE_WRITE = "scheduling.resource.write"
APPOINTMENT_READ = "scheduling.appointment.read"
APPOINTMENT_WRITE = "scheduling.appointment.write"
APPOINTMENT_OVERRIDE = "scheduling.appointment.override"
PROPOSAL_APPROVE = "scheduling.proposal.approve"
ALL = frozenset(
    {RESOURCE_READ, RESOURCE_WRITE, APPOINTMENT_READ, APPOINTMENT_WRITE, APPOINTMENT_OVERRIDE, PROPOSAL_APPROVE}
)
