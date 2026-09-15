import Link from "next/link";

/** Unknown dashboard routes: same glass aesthetic, no fixture content. */
export default function DemoNotFound(): React.ReactElement {
  return (
    <main className="content">
      <section className="error-panel">
        <h2>Vista no encontrada</h2>
        <p>
          Esa ruta no existe en el panel demo. <code>404 · route_not_found</code>
        </p>
        <Link className="ghost-btn" href="/">Volver al resumen</Link>
      </section>
    </main>
  );
}
