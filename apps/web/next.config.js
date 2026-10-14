const prod = process.env.NODE_ENV === "production";

// Next.js injects inline bootstrap scripts, so script-src needs 'unsafe-inline' (a nonce-based CSP
// via middleware would be stricter; tracked as a known limitation). Everything else is locked down.
const csp = [
  "default-src 'self'",
  "img-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  `script-src 'self' 'unsafe-inline'${prod ? "" : " 'unsafe-eval'"}`,
  "connect-src 'self'",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

/** @type {import('next').NextConfig} */
module.exports = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    const h = [
      { key: "Content-Security-Policy", value: csp },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
    ];
    if (prod) h.push({ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" });
    return [{ source: "/:path*", headers: h }];
  },
};
