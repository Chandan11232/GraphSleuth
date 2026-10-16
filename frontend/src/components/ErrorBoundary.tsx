import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/** Shows the error instead of a blank page if a view crashes while rendering. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('View crashed', error, info.componentStack)
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="p-6">
        <div className="max-w-2xl rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">This page hit an error while rendering.</p>
          <pre className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">{this.state.error.message}</pre>
          <button className="mt-3 text-primary underline" onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      </div>
    )
  }
}
