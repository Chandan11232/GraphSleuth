/** The logo: three nodes, one of them lit. Drawn in currentColor so it sits on ink or paper. */
export function Mark({ className = 'h-6 w-6' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <path d="M7 24 16 8l9 16" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="7" cy="24" r="3.2" fill="hsl(var(--ink))" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="25" cy="24" r="3.2" fill="hsl(var(--ink))" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="16" cy="8" r="4.4" fill="hsl(var(--signal))" />
    </svg>
  )
}

export function Wordmark({ className = '' }: { className?: string }) {
  return (
    <span className={`font-display text-[17px] font-bold leading-none tracking-[-0.02em] ${className}`}>
      GraphSleuth
    </span>
  )
}
