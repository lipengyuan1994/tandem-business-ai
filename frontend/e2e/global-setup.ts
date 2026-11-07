// Before every run: recreate the synthetic demo data and give the seeded staff a fresh dev Keycloak login.
// Default target: the dev stack. For the production rehearsal (deploy/scripts/rehearse.sh) set
//   E2E_COMPOSE_FILE=deploy/rehearsal/compose.yml  E2E_ENV_FILE=deploy/rehearsal/.env  E2E_FRESH_ACCOUNTS=1
// (the stack must run with APP_ENV=dev because `dev-account` refuses to run in production mode).
import { execFileSync } from "node:child_process"
import { existsSync, readFileSync, rmSync } from "node:fs"
import { resolve } from "node:path"
import { AUTH_DIR, REPO_ROOT, writeAccount, type Account } from "./support/accounts.ts"

const composeFile = process.env.E2E_COMPOSE_FILE ?? "deploy/compose/compose.dev.yml"
const envFile = process.env.E2E_ENV_FILE
const fresh = process.env.E2E_FRESH_ACCOUNTS === "1"

function envValue(path: string, key: string): string {
  const line = readFileSync(path, "utf8").split("\n").find((l) => l.startsWith(`${key}=`))
  if (!line) throw new Error(`${key} not found in ${path}`)
  return line.slice(key.length + 1).trim()
}

function exec(args: string[], env: NodeJS.ProcessEnv = process.env, passthrough: string[] = []): string {
  const compose = ["compose", "-f", composeFile, ...(envFile ? ["--env-file", envFile] : []), "exec", "-T", ...passthrough, "api", "python", "-m", "app.cli", ...args]
  return execFileSync("docker", compose, { cwd: REPO_ROOT, env, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] })
}

function freshLogin(email: string, name: Account["name"]): void {
  const out = exec(["dev-account", "--email", email])
  const temporary = /^temporary_password=(.+)$/m.exec(out)?.[1]
  if (!temporary) throw new Error(`dev-account did not issue a temporary password for ${email}`)
  writeAccount(name, email, temporary) // stored in the gitignored e2e/.auth; never printed
}

export default function globalSetup(): void {
  // Sessions from an earlier run are stale once the demo data is recreated.
  for (const f of ["morgan.state.json", "casey.state.json", "ellery.state.json", ...(fresh ? [] : ["morgan.json"])]) rmSync(resolve(AUTH_DIR, f), { force: true })

  // The production api container has no migration credentials; reset needs them, so pass them by name only.
  const env = { ...process.env }
  const passthrough: string[] = []
  if (envFile) {
    if (!existsSync(envFile)) throw new Error(`E2E_ENV_FILE not found: ${envFile}`)
    env.DATABASE_MIGRATION_URL = `postgresql+psycopg://smb_owner:${envValue(envFile, "SMB_OWNER_PASSWORD")}@postgres:5432/smb`
    passthrough.push("-e", "DATABASE_MIGRATION_URL")
  }
  exec(["seed", "--reset"], env, passthrough)
  if (fresh) freshLogin("morgan@example.com", "morgan")
  freshLogin("casey@example.com", "casey")
  freshLogin("ellery.dental@example.com", "ellery")
}
