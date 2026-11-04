export function readableAction(action: string): string {
  return action.split(".").map((part) => part.replaceAll("_", " ")).join(" · ")
}
