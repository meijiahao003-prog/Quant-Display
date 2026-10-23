import html from "../public/index.html?raw";

/** Serve the existing dashboard without changing its layout or interactions. */
export function GET() {
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}
