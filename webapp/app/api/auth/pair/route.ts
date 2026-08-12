import { NextRequest, NextResponse } from "next/server";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

export async function POST(request: NextRequest): Promise<Response> {
  try {
    const response = await fetch(`${daemonUrl}/auth/pair`, {
      method: "POST",
      headers: { accept: "application/json", "content-type": request.headers.get("content-type") ?? "application/json" },
      body: await request.text(),
    });
    return new NextResponse(await response.text(), { status: response.status, headers: { "content-type": response.headers.get("content-type") ?? "application/json" } });
  } catch (error: unknown) {
    return NextResponse.json({ detail: { error: "daemon_unreachable", message: String(error) } }, { status: 502 });
  }
}
