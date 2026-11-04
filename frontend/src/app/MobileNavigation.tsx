import type { ReactNode } from "react"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"

export function MobileNavigation({ title, description, children, onClose, returnFocus }: {
  title: string; description: string; children: ReactNode; onClose: () => void; returnFocus: () => void
}) {
  return <Sheet open onOpenChange={(open) => { if (!open) onClose() }}>
    <SheetContent onCloseAutoFocus={(event) => { event.preventDefault(); returnFocus() }} side="left" className="overflow-y-auto">
      <SheetHeader><SheetTitle>{title}</SheetTitle><SheetDescription>{description}</SheetDescription></SheetHeader>
      {children}
    </SheetContent>
  </Sheet>
}
