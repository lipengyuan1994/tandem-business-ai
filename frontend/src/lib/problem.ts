// RFC 9457 problem details from the API (04-repository-and-conventions.md#api-conventions).
export interface FieldError {
  field: string
  code: string
}

export interface ProblemBody {
  type?: string
  title: string
  status: number
  code: string
  detail?: string
  request_id?: string | null
  errors?: FieldError[]
  [extra: string]: unknown
}

export class ApiProblem extends Error {
  readonly problem: ProblemBody

  constructor(problem: ProblemBody) {
    super(problem.detail ?? problem.title)
    this.name = "ApiProblem"
    this.problem = problem
  }

  get status(): number {
    return this.problem.status
  }

  get code(): string {
    return this.problem.code
  }

  get isConflict(): boolean {
    return this.problem.status === 409
  }

  fieldErrors(): Record<string, string> {
    return Object.fromEntries((this.problem.errors ?? []).map((e) => [e.field, e.code]))
  }
}

export function isProblemBody(value: unknown): value is ProblemBody {
  return typeof value === "object" && value !== null && "code" in value && "status" in value
}

export function toApiProblem(body: unknown, status: number): ApiProblem {
  if (isProblemBody(body)) return new ApiProblem(body)
  return new ApiProblem({ title: "Unexpected response", status, code: "client.unexpected_response" })
}
