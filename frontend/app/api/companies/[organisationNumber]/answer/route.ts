import { NextResponse } from "next/server";

type RouteContext = { params: Promise<{ organisationNumber: string }> };

export async function POST(request: Request, context: RouteContext) {
  const baseUrl = process.env.SIGNALPOST_API_URL?.trim().replace(/\/$/, "");
  if (!baseUrl) {
    return NextResponse.json({ error: "Live backend is not configured" }, { status: 503 });
  }

  const { organisationNumber } = await context.params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "A JSON question body is required" }, { status: 400 });
  }

  try {
    const response = await fetch(`${baseUrl}/api/v1/companies/${encodeURIComponent(organisationNumber)}/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const payload = await response.json();
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    return NextResponse.json({ error: "Live answer service is unavailable", detail: error instanceof Error ? error.message : "Unknown backend error" }, { status: 503 });
  }
}
