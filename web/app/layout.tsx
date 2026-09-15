import type { Metadata } from "next";
import React from "react";
import "./demo.css";

export const metadata: Metadata = {
  title: "HighEstate — Panel demo comercial",
  description: "Panel local con datos reales de PostgreSQL: cuentas demo, bandeja de entrada, prospectos, propiedades, embudo, agenda y actividad.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
