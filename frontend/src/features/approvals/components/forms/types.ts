export interface ActionFormProps {
  payload: Record<string, unknown>
  pending: boolean
  onSubmit: (payload: Record<string, unknown>) => void
  onCancel: () => void
}
