import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "@tanstack/react-router"
import { useState } from "react"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ApiProblem } from "@/lib/problem"
import { buildRouter } from "./router"

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        // Do not retry client errors (403, 404, 409, 422); retry transient ones twice.
        retry: (count, error) => !(error instanceof ApiProblem && error.status < 500) && count < 2,
      },
    },
  })
}

export function App() {
  const [queryClient] = useState(makeQueryClient)
  const [router] = useState(() => buildRouter(queryClient))
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>
  )
}
