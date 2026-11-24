import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Test-only disk read without adding Node globals to the browser TypeScript project.
const { readFileSync } = await vi.importActual<{ readFileSync: (path: string, encoding: string) => string }>("node:fs")
const source = readFileSync("../deploy/keycloak/themes/tandem-demo/login/resources/js/guest-login.js", "utf8")
const demoWindow = window as Window & { tandemDemoCredentials?: { email: string; password: string } | null }
const runScript = () => window.eval(source)

beforeEach(() => {
  vi.spyOn(document, "readyState", "get").mockReturnValue("complete")
  demoWindow.tandemDemoCredentials = { email: "demo.guest@example.com", password: "Public-test-2026" }
  document.body.innerHTML = '<form id="kc-form-login"><input name="username"><input name="password" type="password"><button>Sign in</button></form>'
})
afterEach(() => {
  document.body.innerHTML = ""
  delete demoWindow.tandemDemoCredentials
  vi.restoreAllMocks()
})

const field = (name: string) => document.querySelector<HTMLInputElement>(`input[name="${name}"]`)!

describe("Keycloak public judge theme script", () => {
  it("fills empty credentials, shows the notice and lets staff clear and type their own", () => {
    runScript()
    expect(field("username").value).toBe("demo.guest@example.com")
    expect(field("password").value).toBe("Public-test-2026")
    const notice = document.querySelector('[role="note"]')!
    expect(notice.textContent).toContain("Explore the demo. Sign in with the prefilled guest account, then choose a sample business.")
    expect(notice.textContent).toContain("cannot approve proposals you asked the assistant")
    expect(notice.textContent).toContain("A product of Boston AI Forward")
    expect(notice.querySelector("a")?.href).toBe("https://bostonaiforward.com/inquiry.html")
    expect(notice.textContent).not.toMatch(/judges?|open-?dl|hackathon/i)
    expect(notice.nextElementSibling?.id).toBe("kc-form-login")
    field("username").value = "morgan@example.com"
    field("password").value = "staff-password"
    field("username").dispatchEvent(new Event("input"))
    expect(field("username").value).toBe("morgan@example.com")
    expect(field("password").value).toBe("staff-password")
  })

  it.each(["username", "password"])("preserves existing %s and does not mix judge and staff credentials", (name) => {
    field(name).value = "existing-value"
    runScript()
    expect(field(name).value).toBe("existing-value")
    expect(field(name === "username" ? "password" : "username").value).toBe("")
  })

  it.each(["username", "password"])("does not touch OTP, reset, or single-field pages missing %s", (name) => {
    field(name).remove()
    runScript()
    expect(document.querySelector('[role="note"]')).toBeNull()
    expect(document.querySelector("input")?.value).toBe("")
  })

  it("leaves an unconfigured theme alone", () => {
    demoWindow.tandemDemoCredentials = null
    runScript()
    expect(field("username").value).toBe("")
    expect(document.querySelector('[role="note"]')).toBeNull()
  })
})
