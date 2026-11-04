import { setupServer } from "msw/node"

/** Shared MSW server; tests register handlers with server.use(...). Unhandled requests fail the test. */
export const server = setupServer()
