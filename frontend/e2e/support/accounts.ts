// Test accounts for the local dev stack. Values live in gitignored files and are never printed.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"
import { totp, totpStep } from "./totp.ts"

export const AUTH_DIR = resolve(import.meta.dirname, "../.auth")
export const REPO_ROOT = resolve(import.meta.dirname, "../../..")

export interface Account {
  name: "morgan" | "casey"
  email: string
  password: string
  totpSecret?: string
  /** Where changes (new password, TOTP secret) are saved; Morgan's come from .dev-credentials. */
  save: (a: Account) => void
}

export const storageState = (name: Account["name"]) => resolve(AUTH_DIR, `${name}.state.json`)

function readKeyValues(path: string): Record<string, string> {
  return Object.fromEntries(readFileSync(path, "utf8").split("\n").filter((l) => l.includes("=") && !l.startsWith("#")).map((l) => {
    const i = l.indexOf("=")
    return [l.slice(0, i).trim(), l.slice(i + 1).trim()]
  }))
}

const morganPath = resolve(AUTH_DIR, "morgan.json")

/**
 * Morgan, owner of the demo business. On the dev stack this is the owner created by `bootstrap-business`
 * (credentials in the gitignored .dev-credentials). Against a fresh stack (E2E_FRESH_ACCOUNTS=1, the production
 * rehearsal) global setup gives Morgan a new login, saved in e2e/.auth/morgan.json.
 */
export function morgan(): Account {
  if (existsSync(morganPath)) {
    const data = JSON.parse(readFileSync(morganPath, "utf8")) as { email: string; password: string; totpSecret?: string }
    return { name: "morgan", ...data, save: (a) => writeAccount("morgan", a.email, a.password, a.totpSecret) }
  }
  const path = resolve(REPO_ROOT, ".dev-credentials")
  if (!existsSync(path)) throw new Error(".dev-credentials missing: bootstrap the dev owner first (README, Development)")
  const kv = readKeyValues(path)
  return {
    name: "morgan", email: kv.email!, password: kv.password!, totpSecret: kv.totp_secret,
    save: (a) => writeFileSync(path, `# Local dev only. Owner of Example Cycle Works in the dev Keycloak. Never commit.\nemail=${a.email}\npassword=${a.password}\ntotp_secret=${a.totpSecret ?? ""}\n`, { mode: 0o600 }),
  }
}

const caseyPath = resolve(AUTH_DIR, "casey.json")

/** A seeded operator given a fresh Keycloak account by global setup (`app.cli dev-account`). */
export function casey(): Account {
  const data = JSON.parse(readFileSync(caseyPath, "utf8")) as { email: string; password: string; totpSecret?: string }
  return { name: "casey", ...data, save: (a) => writeAccount("casey", a.email, a.password, a.totpSecret) }
}

export function writeAccount(name: Account["name"], email: string, password: string, totpSecret?: string): void {
  mkdirSync(AUTH_DIR, { recursive: true })
  writeFileSync(resolve(AUTH_DIR, `${name}.json`), JSON.stringify({ email, password, totpSecret }), { mode: 0o600 })
}

/** A TOTP code from a period this account has not used yet (Keycloak rejects a reused code). */
export async function freshCode(a: Account): Promise<string> {
  mkdirSync(AUTH_DIR, { recursive: true })
  const path = resolve(AUTH_DIR, `${a.name}.otp-step`)
  const last = existsSync(path) ? Number(readFileSync(path, "utf8")) : -1
  while (totpStep() <= last) await new Promise((r) => setTimeout(r, 1000))
  const step = totpStep()
  writeFileSync(path, String(step))
  return totp(a.totpSecret!, step)
}
