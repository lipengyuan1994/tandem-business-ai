// RFC 6238 TOTP (SHA-1, 6 digits, 30 s), matching the dev realm's OTP policy.
import { createHmac } from "node:crypto"

function base32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
  let bits = ""
  for (const ch of secret.replace(/[\s=]/g, "").toUpperCase()) {
    const v = alphabet.indexOf(ch)
    if (v < 0) throw new Error("invalid base32 secret")
    bits += v.toString(2).padStart(5, "0")
  }
  const bytes = bits.match(/.{8}/g) ?? []
  return Buffer.from(bytes.map((b) => parseInt(b, 2)))
}

export function totpStep(now = Date.now()): number {
  return Math.floor(now / 30_000)
}

export function totp(secret: string, step = totpStep()): string {
  const msg = Buffer.alloc(8)
  msg.writeBigUInt64BE(BigInt(step))
  const h = createHmac("sha1", base32(secret)).update(msg).digest()
  const o = h[h.length - 1]! & 0x0f
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0")
}
