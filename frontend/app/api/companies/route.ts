import { promises as fs } from "node:fs";
import path from "node:path";
import { NextResponse } from "next/server";

type JsonRecord = Record<string, unknown>;
type Claim = { field: string; value: unknown; value_details?: unknown; availability?: string; confidence?: number; evidence_ids?: string[] };

const artifactCandidates = [
  path.resolve(process.cwd(), "../backend/submission/smoke-100/envelopes.full.jsonl"),
  path.resolve(process.cwd(), "backend/submission/smoke-100/envelopes.full.jsonl"),
  path.resolve(process.cwd(), "../backend/submission/smoke-100/envelopes.jsonl"),
  path.resolve(process.cwd(), "backend/submission/smoke-100/envelopes.jsonl"),
];

function record(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}

function text(value: unknown, fallback = "Not available") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function starterAvailability(status: unknown) {
  switch (status) {
    case "available": return "available";
    case "not_found":
    case "not_available": return "not_available";
    case "blocked": return "blocked";
    case "failed":
    case "source_error": return "failed";
    case "not_applicable": return "not_applicable";
    default: return "not_checked";
  }
}

function starterEvidence(envelope: JsonRecord, field: string): JsonRecord {
  const profile = record(envelope.profile);
  const evidence = record(profile.evidence);
  const item = record(evidence[field]);
  const value = item.value;
  const id = `starter-${field}`;
  return {
    id,
    source_url: text(item.source_url, "https://builderr.ai/docs/signalpost-evaluation-harness.md"),
    source_class: text(item.source_class ?? item.source_type, "public_source"),
    retrieved_at: item.retrieved_at ?? envelope.completed_at,
    content_sha256: text(item.content_sha256, "starter-shape-no-hash"),
    claim_span: text(item.note ?? (typeof value === "string" ? value : JSON.stringify(value)), "source status"),
    effective_at: item.effective_at,
  };
}

function starterClaim(envelope: JsonRecord, field: string, value: unknown, sourceField = field): Claim {
  const profile = record(envelope.profile);
  const evidence = record(profile.evidence);
  const item = record(evidence[sourceField]);
  return {
    field,
    value,
    availability: starterAvailability(item.status),
    confidence: item.status === "available" ? 1 : 0,
    evidence_ids: [`starter-${sourceField}`],
  };
}

function adaptEnvelope(envelope: JsonRecord): JsonRecord {
  if (Array.isArray(envelope.claims) && Array.isArray(envelope.evidence)) return envelope;
  const profile = record(envelope.profile);
  const profileEvidence = record(profile.evidence);
  const moduleValue = (field: string) => record(profileEvidence[field]).value;
  const financials = record(moduleValue("financials"));
  const financialRecords = Array.isArray(financials.records) ? financials.records : [];
  const orderedFinancials = [...financialRecords].sort((left, right) => {
    const leftDate = text(record(left).period && record(record(left).period).tilDato, "");
    const rightDate = text(record(right).period && record(record(right).period).tilDato, "");
    return rightDate.localeCompare(leftDate);
  });
  const roles = record(moduleValue("roles"));
  const locations = record(moduleValue("locations"));
  const websiteRecord = record(profileEvidence.website);
  const websiteValue = record(websiteRecord.value);
  const websiteUrl = text(websiteValue.final_url ?? websiteValue.requested_url ?? profile.website, "");
  const claims: Claim[] = [
    starterClaim(envelope, "legal_identity", moduleValue("registry"), "registry"),
    starterClaim(envelope, "latest_annual_accounts", orderedFinancials[0] ?? null, "financials"),
    starterClaim(envelope, "accounts_history", orderedFinancials.slice(1), "financials"),
    starterClaim(envelope, "leadership", roles.roles ?? moduleValue("roles"), "roles"),
    starterClaim(envelope, "registered_workplaces", locations.locations ?? moduleValue("locations"), "locations"),
    starterClaim(envelope, "group_links", moduleValue("group"), "group"),
    starterClaim(envelope, "public_brand", { title: websiteValue.title, description: websiteValue.description }, "website"),
    { ...starterClaim(envelope, "official_website", websiteUrl, "website"), value_details: websiteValue },
    starterClaim(envelope, "company_profiles", websiteValue.social_links ?? [], "website"),
    starterClaim(envelope, "hiring", { checked: false }, "website"),
    starterClaim(envelope, "public_activity", { checked: false }, "website"),
  ];
  const evidence = Object.keys(profileEvidence)
    .filter((field) => ["registry", "financials", "roles", "locations", "group", "website"].includes(field))
    .map((field) => starterEvidence(envelope, field));
  const unknownAreas = claims
    .filter((claim) => claim.availability !== "available" && claim.availability !== "not_applicable")
    .map((claim) => claim.field);
  const runId = text(envelope.run_id, "unknown-run");
  const terminalStatus = envelope.state === "complete" ? "completed" : text(envelope.state, "source_error");
  return {
    ...envelope,
    run: {
      run_id: runId,
      started_at: envelope.started_at,
      completed_at: envelope.completed_at,
      terminal_status: terminalStatus,
    },
    claims,
    evidence,
    errors: [],
    operations: record(profile.run_metrics),
    external: { handles: [], company_site: { posts: [], jobs: [] } },
    changes: [],
    unknown_areas: unknownAreas,
  };
}

function claimMap(envelope: JsonRecord) {
  const map = new Map<string, Claim>();
  for (const claim of Array.isArray(envelope.claims) ? envelope.claims : []) {
    const item = record(claim) as Claim;
    if (typeof item.field === "string") map.set(item.field, item);
  }
  return map;
}

function claimValue(claims: Map<string, Claim>, field: string) {
  return claims.get(field)?.value;
}

function claimAvailability(claims: Map<string, Claim>, field: string) {
  return claims.get(field)?.availability ?? "not_checked";
}

function availabilityLabel(state: string, availableLabel: string) {
  switch (state) {
    case "available": return availableLabel;
    case "not_available": return "Not available";
    case "blocked": return "Blocked";
    case "ambiguous": return "Ambiguous";
    case "failed": return "Failed";
    default: return "Not checked";
  }
}

function availabilityTone(state: string): "good" | "muted" | "warn" {
  return state === "available" ? "good" : state === "not_available" || state === "not_checked" ? "muted" : "warn";
}

function formatDate(value: unknown, withTime = true) {
  if (typeof value !== "string" || !value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
    timeZone: "UTC",
  }).format(date);
}

function formatNok(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("en-GB", { style: "currency", currency: "NOK", maximumFractionDigits: 0 }).format(value)
    : "Not available";
}

function addressText(address: unknown) {
  const item = record(address);
  const street = Array.isArray(item.adresse) ? item.adresse.filter(Boolean).join(", ") : text(item.adresse, "");
  const locality = [item.poststed, item.postnummer, item.kommune].filter((part) => typeof part === "string" && part).join(", ");
  return [street, locality].filter(Boolean).join(" · ") || "Address not available";
}

function personName(value: unknown) {
  return Array.isArray(value) ? value.join(", ") : text(value);
}

function hostname(value: string) {
  try { return new URL(value).hostname.replace(/^www\./, ""); } catch { return value; }
}

function sourceKind(sourceClass: string) {
  if (sourceClass.includes("annual") || sourceClass.includes("account")) return "Accounts registry";
  if (sourceClass.includes("role")) return "Leadership registry";
  if (sourceClass.includes("company") || sourceClass.includes("website")) return "Company-owned";
  if (sourceClass.includes("nav")) return "Public jobs feed";
  if (sourceClass.includes("official")) return "Official registry";
  return "Public source";
}

function mapEnvelope(envelope: JsonRecord) {
  envelope = adaptEnvelope(envelope);
  const claims = claimMap(envelope);
  const profile = record(envelope.profile);
  const raw = record(profile.raw);
  const evidenceRecords = Array.isArray(envelope.evidence) ? envelope.evidence.map(record) : [];
  const registryLive = record(evidenceRecords.find((item) => item.field === "registry_live")?.value);
  const address = record(registryLive.business_address ?? {
    adresse: raw["forretningsadresse.adresse"],
    poststed: raw["forretningsadresse.poststed"],
    postnummer: raw["forretningsadresse.postnummer"],
    kommune: raw["forretningsadresse.kommune"],
  });
  const latestAccounts = record(claimValue(claims, "latest_annual_accounts"));
  const period = record(latestAccounts.period);
  const location = [text(profile.municipality, "Norway"), "Norway"].filter((part, index, parts) => part && parts.indexOf(part) === index).join(", ");
  const lastChecked = formatDate(envelope.completed_at ?? record(envelope.run).completed_at);
  const coverageFields = ["legal_identity", "latest_annual_accounts", "leadership", "company_profiles", "hiring"];
  const coverageScore = coverageFields.filter((field) => claimAvailability(claims, field) === "available").length;
  const status = coverageScore === 5 ? "Verified" : coverageScore >= 3 ? "Partial" : "Needs review";
  const websiteClaim = claims.get("official_website");
  const websiteClaimValue = websiteClaim?.value;
  // Scored website claims expose a URL string. The legacy website object is
  // retained in value_details for the UI's social links and fallback fields.
  const websiteValue = record(websiteClaim?.value_details ?? websiteClaimValue);
  const website = text(typeof websiteClaimValue === "string" ? websiteClaimValue : websiteValue.final_url ?? websiteValue.requested_url ?? profile.website, "");
  const websiteDomain = website ? hostname(website) : "";
  const leadershipValue = claimValue(claims, "leadership");
  const workplacesValue = claimValue(claims, "registered_workplaces");
  const hiringValue = record(claimValue(claims, "hiring"));
  const activityValue = record(claimValue(claims, "public_activity"));
  const briefValue = record(claimValue(claims, "company_brief"));
  const companyProfilesValue = record(claimValue(claims, "company_profiles"));
  const sourceEntries = evidenceRecords;
  const referencedEvidence = new Set<string>();
  for (const field of [...coverageFields, "official_website", "public_activity"]) {
    for (const id of claims.get(field)?.evidence_ids ?? []) referencedEvidence.add(id);
  }
  const sources = sourceEntries
    .map((source) => record(source))
    .filter((source) => typeof source.id === "string" && typeof source.source_url === "string" && (referencedEvidence.size === 0 || referencedEvidence.has(source.id)))
    .filter((source, index, all) => all.findIndex((candidate) => candidate.source_url === source.source_url) === index)
    .slice(0, 10)
    .map((source) => ({
      name: hostname(text(source.source_url, "Public source")),
      kind: sourceKind(text(source.source_class, "public_source")),
      checked: formatDate(source.retrieved_at),
      url: text(source.source_url, "#"),
    }));
  const accountSource = sources.find((source) => source.kind === "Accounts registry")?.name ?? "Official accounts registry";
  const summary = text(briefValue.summary, `Registered in ${text(profile.municipality, "Norway")}. Industry: ${text(profile.industry_label)}.`);
  const socialLinks = Array.isArray(websiteValue.social_links) ? websiteValue.social_links : [];
  const roles = Array.isArray(leadershipValue) ? leadershipValue : [];
  const locations = Array.isArray(workplacesValue) ? workplacesValue : [];
  const postings = [
    ...(Array.isArray(hiringValue.postings) ? hiringValue.postings : []),
    ...(Array.isArray(hiringValue.company_site_postings) ? hiringValue.company_site_postings : []),
    ...(Array.isArray(hiringValue.nav_ads) ? hiringValue.nav_ads : []),
  ];
  const activityItems = Array.isArray(activityValue.items) ? activityValue.items : [];
  const availability = [
    ["Legal identity", "legal_identity", "Verified"],
    ["Accounts", "latest_annual_accounts", "Available"],
    ["Leadership", "leadership", "Available"],
    ["Official profiles", "company_profiles", "Found"],
    ["Hiring signals", "hiring", "Current"],
  ].map(([label, field, goodLabel]) => {
    const state = claimAvailability(claims, field);
    return { label, value: availabilityLabel(state, goodLabel), tone: availabilityTone(state) };
  });

  const mappedChanges = (Array.isArray(envelope.changes) ? envelope.changes : []).map((change) => {
    const item = record(change);
    return { label: text(item.label ?? item.field, "Backend refresh"), detail: text(item.detail ?? item.description, "No further detail was provided."), date: formatDate(item.date ?? item.detected_at, false), tone: "review" as const };
  });

  return {
    id: text(envelope.organisation_number),
    name: text(profile.name),
    legalName: text(profile.name),
    location,
    sector: text(profile.industry_label),
    status,
    coverage: `${coverageScore}/5`,
    coverageScore,
    lastChecked,
    website: websiteDomain,
    summary,
    registration: text(envelope.organisation_number),
    founded: typeof raw.stiftelsesdato === "string" && raw.stiftelsesdato ? raw.stiftelsesdato.slice(0, 4) : "Not available",
    account: {
      period: period.tilDato ? `Year ended ${String(period.tilDato).slice(0, 4)}` : "Latest period not available",
      filed: "Not available",
      revenue: formatNok(latestAccounts.revenue),
      operatingResult: formatNok(latestAccounts.operating_result),
      annualResult: formatNok(latestAccounts.annual_result),
      assets: formatNok(latestAccounts.assets),
      debt: formatNok(latestAccounts.debt),
      source: accountSource,
      sourceUrl: sources.find((source) => source.kind === "Accounts registry")?.url,
    },
    leaders: roles.map((leader) => ({ name: personName(record(leader).name), role: text(record(leader).role), since: text(record(leader).last_changed, "Not available").slice(0, 4) })),
    workplaces: (locations.length ? locations : [{ address }]).map((place, index) => ({ label: index === 0 ? "Registered workplace" : `Workplace ${index + 1}`, address: addressText(record(place).address ?? place), type: index === 0 ? "Registered" : "Operating" })),
    profiles: socialLinks.map((link: unknown) => ({ network: hostname(text(link)), handle: text(link), verified: false })),
    hiring: postings.map((posting) => {
      const item = record(posting);
      return { role: text(item.title ?? item.position ?? item.job_title), team: text(item.business_area ?? item.category, "Public listing"), posted: formatDate(item.published ?? item.published_at ?? item.created, false), status: item.status === "closed" ? "Closed" : "Open" };
    }),
    activity: activityItems.map((activity) => {
      const item = record(activity);
      return { title: text(item.title ?? item.headline), detail: text(item.detail ?? item.description), date: formatDate(item.date ?? item.published_at, false), source: text(item.source ?? websiteDomain, "Public source") };
    }),
    sources,
    changes: mappedChanges.length ? mappedChanges : [{ label: "Refresh status", detail: "No material changes were reported by the latest backend run.", date: lastChecked, tone: "steady" as const }],
    availability,
    backend: {
      runId: text(envelope.run_id ?? record(envelope.run).run_id),
      state: text(envelope.state, "completed"),
      errorCount: Array.isArray(envelope.errors) ? envelope.errors.length : 0,
      unknownAreas: Array.isArray(envelope.unknown_areas) ? envelope.unknown_areas : [],
      companyProfilesChecked: companyProfilesValue.checked === true,
    },
  };
}

async function readArtifact() {
  for (const candidate of artifactCandidates) {
    try {
      return await fs.readFile(candidate, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
  throw new Error("The backend JSONL artifact was not found.");
}

async function readLiveApi() {
  const baseUrl = process.env.SIGNALPOST_API_URL?.trim().replace(/\/$/, "");
  if (!baseUrl) return null;
  const response = await fetch(`${baseUrl}/api/v1/companies?limit=100&sort=complete`, { cache: "no-store" });
  const payload = await response.json() as { data?: Array<{ envelope?: JsonRecord }>; meta?: { total?: number } };
  if (!response.ok) throw new Error(`Live backend returned HTTP ${response.status}`);
  const envelopes = (payload.data ?? []).map((item) => record(item.envelope)).filter((envelope) => Boolean(envelope.organisation_number));
  if (!envelopes.length) throw new Error("Live backend returned no company profiles");
  return { envelopes, source: `${baseUrl}/api/v1/companies`, total: payload.meta?.total ?? envelopes.length };
}

export async function GET() {
  try {
    // A configured but unreachable live API must not blank the UI: fall back to the committed artifact.
    const live = await readLiveApi().catch(() => null);
    const content = live ? null : await readArtifact();
    const envelopes = live?.envelopes ?? content!.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line) as JsonRecord);
    const companies = envelopes.map(mapEnvelope);
    const firstRun = record(adaptEnvelope(envelopes[0] ?? {}).run);
    return NextResponse.json({
      companies,
      source: live?.source ?? "backend/submission/smoke-100/envelopes.jsonl",
      total: live?.total ?? companies.length,
      run: { id: text(firstRun.run_id), completedAt: formatDate(firstRun.completed_at), status: text(firstRun.terminal_status, "completed") },
    });
  } catch (error) {
    return NextResponse.json({ error: "Backend data is unavailable", detail: error instanceof Error ? error.message : "Unknown backend read error" }, { status: 503 });
  }
}
