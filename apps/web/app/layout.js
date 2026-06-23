import "./globals.css";

export const metadata = {
  title: "2nd Act Capital",
  description: "A private community for the post-liquidity investor.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
