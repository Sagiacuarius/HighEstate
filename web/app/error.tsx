"use client";

import React from "react";

/** Segment-level failures: envelope-style panel with a retry action. */
export default function DemoError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.ReactElement {
  return (
    <main className="content">
      <section className="error-panel">
        <h2>El panel no pudo renderizar</h2>
        <p>
          <code>{error.message}</code> · los datos persistidos no cambiaron.
        </p>
        <button className="ghost-btn" onClick={() => reset()}>Reintentar</button>
      </section>
    </main>
  );
}
