// Action-specific edit forms (task P3-13). Action types without an entry use the generic RevisionEditor.
import type { ComponentType } from "react"
import { AdjustStockForm } from "./AdjustStockForm"
import { BookingForm } from "./BookingForm"
import { ReplyForm } from "./ReplyForm"
import type { ActionFormProps } from "./types"

export const ACTION_FORMS: Record<string, ComponentType<ActionFormProps>> = {
  "scheduling.book_appointment": BookingForm,
  "conversations.send_reply": ReplyForm,
  "inventory.adjust_stock": AdjustStockForm,
}

export type { ActionFormProps }
