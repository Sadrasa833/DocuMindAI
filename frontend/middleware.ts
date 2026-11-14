import { NextRequest, NextResponse } from "next/server";

const ACCESS_COOKIE = "documind_access";
const REFRESH_COOKIE = "documind_refresh";

const PROTECTED_PREFIXES = [
  "/dashboard",
  "/documents",
  "/chat",
  "/conversations",
  "/settings",
];

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const hasSession =
    request.cookies.has(ACCESS_COOKIE) || request.cookies.has(REFRESH_COOKIE);

  const isProtected = PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );

  if (isProtected && !hasSession) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/documents/:path*",
    "/chat/:path*",
    "/conversations/:path*",
    "/settings/:path*",
    "/login",
    "/register",
  ],
};
