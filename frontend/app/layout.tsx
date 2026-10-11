import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SignalPost | Company intelligence",
  description: "A source-first company research workspace.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return <html lang="en"><body>{children}</body></html>;
}
