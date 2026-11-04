import { Toaster as Sonner, type ToasterProps } from "sonner"

// Theme follows the `dark` class on <html> (no next-themes dependency).
const Toaster = (props: ToasterProps) => {
  const dark = typeof document !== "undefined" && document.documentElement.classList.contains("dark")
  return (
    <Sonner
      theme={dark ? "dark" : "light"}
      className="toaster group"
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
        } as React.CSSProperties
      }
      {...props}
    />
  )
}

export { Toaster }
