/**
 * Session-level disclaimer state.
 * 
 * Extracted into its own module to avoid circular dependencies
 * between the auth store and ProtectedRoute.
 */

let _disclaimerResolved = false;

export function isDisclaimerResolved(): boolean {
  return _disclaimerResolved;
}

export function setDisclaimerResolved(value: boolean): void {
  _disclaimerResolved = value;
}

export function resetDisclaimerState(): void {
  _disclaimerResolved = false;
}
