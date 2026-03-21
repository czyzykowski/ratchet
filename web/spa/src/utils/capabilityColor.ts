export function capabilityColor(name: string): { background: string; color: string } {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = Math.imul(hash, 31) + name.charCodeAt(i)
    hash |= 0
  }
  const hue = Math.abs(hash) % 360
  return {
    background: `hsl(${hue}, 60%, 92%)`,
    color: `hsl(${hue}, 60%, 30%)`,
  }
}
