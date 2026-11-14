import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "DocuMind AI",
    template: "%s · DocuMind AI",
  },
  description:
    "Upload documents, search them semantically, and get grounded answers with precise citations.",
  applicationName: "DocuMind AI",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
