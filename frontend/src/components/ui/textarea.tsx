import * as React from "react"
import { cn } from "cn"
import { DictationButton } from "@/features/ai/DictationButton"
import type { DictationOptions } from "@/features/ai/dictation-api"

type Props = React.ComponentProps<"textarea"> & { dictation?: boolean | DictationOptions }

function Textarea({ dictation, ...props }: Props) {
  if (dictation) return <DictatableTextarea {...props} options={dictation === true ? {} : dictation} />
  return <PlainTextarea {...props} />
}

function DictatableTextarea({ ref, options, ...props }: React.ComponentProps<"textarea"> & { options: DictationOptions }) {
  const target = React.useRef<HTMLTextAreaElement>(null)
  React.useImperativeHandle(ref, () => target.current!, [])
  return <div className="min-w-0">
    <PlainTextarea {...props} ref={target} />
    <DictationButton target={target} options={options} disabled={props.disabled || props.readOnly} />
  </div>
}

function PlainTextarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
