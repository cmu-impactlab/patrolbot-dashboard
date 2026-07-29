// vitest setup: localStorage and matchMedia exist in jsdom, but matchMedia
// needs a stub in some environments.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

// jsdom has no canvas backend and logs a stack trace every time one is asked
// for. The map canvases already handle a missing 2D context (they simply do
// not paint), so return null quietly and keep test output readable.
HTMLCanvasElement.prototype.getContext =
  (() => null) as typeof HTMLCanvasElement.prototype.getContext;
