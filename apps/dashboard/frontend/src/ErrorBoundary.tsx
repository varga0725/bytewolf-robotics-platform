import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { failed: boolean };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _errorInfo: ErrorInfo): void {
    // Client-side error details are intentionally not rendered: API errors and
    // telemetry payloads may contain operational context that belongs in logs.
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="fatal-error" role="alert">
        <p className="eyebrow">BIZTONSÁGOS HIBAÁLLAPOT</p>
        <h1>A Control Room nézet hibába futott.</h1>
        <p>Nem küldtünk vezérlési parancsot. Töltsd újra az oldalt; a szerveroldali SafetyGate változatlanul aktív.</p>
        <button type="button" className="primary" onClick={() => window.location.reload()}>Oldal újratöltése</button>
      </main>
    );
  }
}
