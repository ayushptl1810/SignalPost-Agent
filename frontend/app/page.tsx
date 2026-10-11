"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type CoverageStatus = "Verified" | "Partial" | "Needs review";
type ProfileSection =
  | "overview"
  | "financials"
  | "leadership"
  | "activity"
  | "evidence"
  | "changes";

type Source = {
  name: string;
  kind: string;
  checked: string;
  url: string;
};

type Company = {
  id: string;
  name: string;
  legalName: string;
  location: string;
  sector: string;
  status: CoverageStatus;
  coverage: string;
  coverageScore: number;
  lastChecked: string;
  website: string;
  summary: string;
  registration: string;
  founded: string;
  account: {
    period: string;
    filed: string;
    revenue: string;
    operatingResult: string;
    annualResult: string;
    assets: string;
    debt: string;
    source: string;
    sourceUrl?: string;
  };
  leaders: Array<{ name: string; role: string; since: string }>;
  workplaces: Array<{ label: string; address: string; type: string }>;
  profiles: Array<{ network: string; handle: string; verified: boolean }>;
  hiring: Array<{ role: string; team: string; posted: string; status: string }>;
  activity: Array<{ title: string; detail: string; date: string; source: string }>;
  sources: Source[];
  changes: Array<{ label: string; detail: string; date: string; tone: "new" | "steady" | "review" }>;
  availability: Array<{ label: string; value: string; tone: "good" | "muted" | "warn" }>;
  backend?: {
    runId: string;
    state: string;
    errorCount: number;
    unknownAreas: string[];
    companyProfilesChecked: boolean;
  };
};

const evidenceAreaLabels: Record<string, string> = {
  public_activity: "public activity",
  group_links: "group links",
  company_profiles: "official company profiles",
  hiring: "hiring signals",
};

function readableEvidenceArea(area: string) {
  return evidenceAreaLabels[area] ?? area.replaceAll("_", " ");
}

const demoCompanies: Company[] = [
  {
    id: "northstar",
    name: "Northstar Foods plc",
    legalName: "Northstar Foods plc",
    location: "Manchester, UK",
    sector: "Food manufacturing",
    status: "Verified",
    coverage: "5/5",
    coverageScore: 5,
    lastChecked: "Today, 09:42",
    website: "northstarfoods.co.uk",
    summary: "Northstar Foods is a Manchester-based food manufacturer supplying chilled prepared meals to UK grocers and hospitality groups.",
    registration: "04182756",
    founded: "2001",
    account: {
      period: "Year ended 31 Dec 2024",
      filed: "18 Sep 2025",
      revenue: "£184.7m",
      operatingResult: "£12.6m",
      annualResult: "£8.9m",
      assets: "£96.4m",
      debt: "£28.1m",
      source: "Companies House filing",
    },
    leaders: [
      { name: "Maya Fairchild", role: "Chief executive officer", since: "2019" },
      { name: "David Okoro", role: "Chief financial officer", since: "2022" },
      { name: "Priya Nair", role: "Company secretary", since: "2021" },
    ],
    workplaces: [
      { label: "Registered office", address: "41 Trafford Park Road, Manchester M17 1AB", type: "Registered" },
      { label: "Production site", address: "18 Atlas Way, Warrington WA5 3TZ", type: "Operating" },
      { label: "Commercial office", address: "28 Great Marlborough Street, London W1F 7H", type: "Operating" },
    ],
    profiles: [
      { network: "LinkedIn", handle: "northstar-foods", verified: true },
      { network: "Instagram", handle: "@northstarfoods", verified: true },
      { network: "X", handle: "@northstarfoods", verified: false },
    ],
    hiring: [
      { role: "Head of supply planning", team: "Operations", posted: "14 May 2026", status: "Open" },
      { role: "Food safety manager", team: "Quality", posted: "06 May 2026", status: "Open" },
      { role: "Commercial analyst", team: "Finance", posted: "22 Apr 2026", status: "Closed" },
    ],
    activity: [
      { title: "Opened a new Midlands distribution partnership", detail: "The company named a new logistics partner for chilled delivery across the Midlands.", date: "28 May 2026", source: "Company newsroom" },
      { title: "Added two senior operations roles", detail: "Both roles are listed on the company-owned careers page and remain open.", date: "14 May 2026", source: "Careers page" },
      { title: "Annual accounts filed", detail: "The 2024 accounts were filed with Companies House after the prior refresh.", date: "18 Sep 2025", source: "Companies House" },
    ],
    sources: [
      { name: "Companies House", kind: "Registry", checked: "09:42 today", url: "https://find-and-update.company-information.service.gov.uk/" },
      { name: "northstarfoods.co.uk", kind: "Company-owned", checked: "09:37 today", url: "https://www.northstarfoods.co.uk/" },
      { name: "LinkedIn company page", kind: "Company-owned profile", checked: "09:34 today", url: "https://www.linkedin.com/" },
      { name: "Google News index", kind: "Public activity", checked: "09:31 today", url: "https://news.google.com/" },
    ],
    changes: [
      { label: "Accounts", detail: "2024 annual accounts are now the latest available filing.", date: "18 Sep 2025", tone: "new" },
      { label: "Leadership", detail: "No director changes found since the prior check.", date: "Checked today", tone: "steady" },
      { label: "Hiring", detail: "Two open roles were added to the company-owned careers page.", date: "14 May 2026", tone: "review" },
    ],
    availability: [
      { label: "Legal identity", value: "Verified", tone: "good" },
      { label: "Accounts", value: "Available", tone: "good" },
      { label: "Leadership", value: "Available", tone: "good" },
      { label: "Official profiles", value: "3 found", tone: "good" },
      { label: "Hiring signals", value: "Current", tone: "good" },
    ],
  },
  {
    id: "solace",
    name: "Solace Health Ltd",
    legalName: "Solace Health Ltd",
    location: "London, UK",
    sector: "Digital health",
    status: "Verified",
    coverage: "5/5",
    coverageScore: 5,
    lastChecked: "Today, 08:58",
    website: "solacehealth.com",
    summary: "Solace Health builds remote monitoring software for specialist care teams.",
    registration: "08927541",
    founded: "2013",
    account: { period: "Year ended 31 Mar 2025", filed: "20 Dec 2025", revenue: "£32.4m", operatingResult: "£2.1m", annualResult: "£1.4m", assets: "£18.8m", debt: "£4.2m", source: "Companies House filing" },
    leaders: [{ name: "Eleanor Vance", role: "Managing director", since: "2018" }],
    workplaces: [{ label: "Registered office", address: "77 Moorgate, London EC2R 6BH", type: "Registered" }],
    profiles: [{ network: "LinkedIn", handle: "solace-health", verified: true }],
    hiring: [{ role: "Senior product designer", team: "Product", posted: "02 Jun 2026", status: "Open" }],
    activity: [{ title: "Published a product update", detail: "A new hospital integration is described on the company blog.", date: "01 Jun 2026", source: "Company blog" }],
    sources: [{ name: "Companies House", kind: "Registry", checked: "08:58 today", url: "https://find-and-update.company-information.service.gov.uk/" }, { name: "solacehealth.com", kind: "Company-owned", checked: "08:54 today", url: "https://solacehealth.com/" }],
    changes: [{ label: "Hiring", detail: "A senior product designer role was added this week.", date: "02 Jun 2026", tone: "new" }],
    availability: [{ label: "Legal identity", value: "Verified", tone: "good" }, { label: "Accounts", value: "Available", tone: "good" }, { label: "Leadership", value: "Limited", tone: "muted" }, { label: "Official profiles", value: "1 found", tone: "good" }, { label: "Hiring signals", value: "Current", tone: "good" }],
  },
  {
    id: "kestrel",
    name: "Kestrel Robotics Ltd",
    legalName: "Kestrel Robotics Ltd",
    location: "Cambridge, UK",
    sector: "Industrial automation",
    status: "Needs review",
    coverage: "3/5",
    coverageScore: 3,
    lastChecked: "Yesterday, 16:12",
    website: "kestrelrobotics.io",
    summary: "Kestrel Robotics develops machine vision systems for warehouse and factory operators.",
    registration: "11264083",
    founded: "2018",
    account: { period: "Year ended 30 Jun 2024", filed: "04 Apr 2025", revenue: "Not reported", operatingResult: "Not reported", annualResult: "Not reported", assets: "£7.3m", debt: "Not reported", source: "Small company filing" },
    leaders: [{ name: "Jon Bell", role: "Director", since: "2018" }],
    workplaces: [{ label: "Registered office", address: "Unit 4, Cambridge Science Park, CB4 0WS", type: "Registered" }],
    profiles: [{ network: "LinkedIn", handle: "kestrel-robotics", verified: false }],
    hiring: [],
    activity: [{ title: "Website is reachable", detail: "The company site is live, but no dated newsroom items were found.", date: "Checked yesterday", source: "Company website" }],
    sources: [{ name: "Companies House", kind: "Registry", checked: "Yesterday, 16:12", url: "https://find-and-update.company-information.service.gov.uk/" }, { name: "kestrelrobotics.io", kind: "Company-owned", checked: "Yesterday, 16:09", url: "https://kestrelrobotics.io/" }],
    changes: [{ label: "Public activity", detail: "No dated public activity was found in the last 90 days.", date: "Checked yesterday", tone: "review" }],
    availability: [{ label: "Legal identity", value: "Verified", tone: "good" }, { label: "Accounts", value: "Partial", tone: "warn" }, { label: "Leadership", value: "Limited", tone: "muted" }, { label: "Official profiles", value: "Unverified", tone: "warn" }, { label: "Hiring signals", value: "None found", tone: "muted" }],
  },
  {
    id: "lumenpay",
    name: "LumenPay Europe Ltd",
    legalName: "LumenPay Europe Ltd",
    location: "Dublin, Ireland",
    sector: "Financial technology",
    status: "Verified",
    coverage: "5/5",
    coverageScore: 5,
    lastChecked: "02 Jun 2026, 14:22",
    website: "lumenpay.eu",
    summary: "LumenPay provides cross-border payment infrastructure for European marketplaces.",
    registration: "IE638492",
    founded: "2016",
    account: { period: "Year ended 31 Dec 2024", filed: "27 Oct 2025", revenue: "€61.8m", operatingResult: "€7.4m", annualResult: "€5.1m", assets: "€43.2m", debt: "€9.7m", source: "CRO filing" },
    leaders: [{ name: "Ronan Keane", role: "Chief executive officer", since: "2020" }, { name: "Anika Shah", role: "Chief financial officer", since: "2023" }],
    workplaces: [{ label: "Registered office", address: "5 Grand Canal Quay, Dublin 2, D02 YT24", type: "Registered" }],
    profiles: [{ network: "LinkedIn", handle: "lumenpay-europe", verified: true }, { network: "X", handle: "@lumenpay", verified: true }],
    hiring: [{ role: "Risk operations lead", team: "Operations", posted: "29 May 2026", status: "Open" }],
    activity: [{ title: "Released a market expansion note", detail: "The company announced support for three additional settlement currencies.", date: "20 May 2026", source: "Company newsroom" }],
    sources: [{ name: "CRO", kind: "Registry", checked: "02 Jun 2026, 14:22", url: "https://core.cro.ie/" }, { name: "lumenpay.eu", kind: "Company-owned", checked: "02 Jun 2026, 14:18", url: "https://lumenpay.eu/" }],
    changes: [{ label: "Operations", detail: "A risk operations leadership role is currently open.", date: "29 May 2026", tone: "new" }],
    availability: [{ label: "Legal identity", value: "Verified", tone: "good" }, { label: "Accounts", value: "Available", tone: "good" }, { label: "Leadership", value: "Available", tone: "good" }, { label: "Official profiles", value: "2 found", tone: "good" }, { label: "Hiring signals", value: "Current", tone: "good" }],
  },
  {
    id: "harbor",
    name: "Harbor & Pine Ltd",
    legalName: "Harbor and Pine Ltd",
    location: "Bristol, UK",
    sector: "Specialty retail",
    status: "Partial",
    coverage: "4/5",
    coverageScore: 4,
    lastChecked: "31 May 2026, 11:06",
    website: "harborandpine.co.uk",
    summary: "Harbor & Pine operates a small network of home and garden stores across the South West.",
    registration: "07310422",
    founded: "2010",
    account: { period: "Year ended 31 Jul 2024", filed: "02 May 2025", revenue: "£19.7m", operatingResult: "£0.8m", annualResult: "£0.4m", assets: "£12.6m", debt: "£6.3m", source: "Companies House filing" },
    leaders: [{ name: "Clara Morris", role: "Director", since: "2010" }],
    workplaces: [{ label: "Registered office", address: "12 Temple Back, Bristol BS1 6FL", type: "Registered" }, { label: "Trading site", address: "24 Bath Road, Bristol BS4 3DA", type: "Operating" }],
    profiles: [{ network: "Instagram", handle: "@harborandpine", verified: true }],
    hiring: [{ role: "Store manager", team: "Retail", posted: "11 May 2026", status: "Open" }],
    activity: [{ title: "Store opening announced", detail: "A new retail location is listed on the company website.", date: "03 Apr 2026", source: "Company website" }],
    sources: [{ name: "Companies House", kind: "Registry", checked: "31 May 2026, 11:06", url: "https://find-and-update.company-information.service.gov.uk/" }, { name: "harborandpine.co.uk", kind: "Company-owned", checked: "31 May 2026, 11:02", url: "https://harborandpine.co.uk/" }],
    changes: [{ label: "Leadership", detail: "Director identity is confirmed, but no start date is disclosed.", date: "Checked 31 May", tone: "review" }],
    availability: [{ label: "Legal identity", value: "Verified", tone: "good" }, { label: "Accounts", value: "Available", tone: "good" }, { label: "Leadership", value: "Partial", tone: "warn" }, { label: "Official profiles", value: "1 found", tone: "good" }, { label: "Hiring signals", value: "Current", tone: "good" }],
  },
  {
    id: "novaforge",
    name: "NovaForge Materials Ltd",
    legalName: "NovaForge Materials Ltd",
    location: "Leeds, UK",
    sector: "Advanced materials",
    status: "Partial",
    coverage: "4/5",
    coverageScore: 4,
    lastChecked: "30 May 2026, 15:40",
    website: "novaforgematerials.com",
    summary: "NovaForge Materials produces recyclable barrier films for industrial packaging.",
    registration: "09841172",
    founded: "2015",
    account: { period: "Year ended 30 Sep 2024", filed: "12 Jun 2025", revenue: "£42.9m", operatingResult: "£3.6m", annualResult: "£2.2m", assets: "£25.1m", debt: "£10.4m", source: "Companies House filing" },
    leaders: [{ name: "Mina Patel", role: "Managing director", since: "2021" }],
    workplaces: [{ label: "Registered office", address: "7 Wellington Place, Leeds LS1 4AP", type: "Registered" }],
    profiles: [{ network: "LinkedIn", handle: "novaforge-materials", verified: true }],
    hiring: [],
    activity: [{ title: "Published an ESG update", detail: "A short sustainability statement is available on the company site.", date: "18 Mar 2026", source: "Company website" }],
    sources: [{ name: "Companies House", kind: "Registry", checked: "30 May 2026, 15:40", url: "https://find-and-update.company-information.service.gov.uk/" }, { name: "novaforgematerials.com", kind: "Company-owned", checked: "30 May 2026, 15:36", url: "https://novaforgematerials.com/" }],
    changes: [{ label: "Hiring", detail: "No open roles were listed on the company careers page.", date: "Checked 30 May", tone: "steady" }],
    availability: [{ label: "Legal identity", value: "Verified", tone: "good" }, { label: "Accounts", value: "Available", tone: "good" }, { label: "Leadership", value: "Partial", tone: "warn" }, { label: "Official profiles", value: "1 found", tone: "good" }, { label: "Hiring signals", value: "None found", tone: "muted" }],
  },
];

const profileSections: Array<{ id: ProfileSection; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "financials", label: "Financials" },
  { id: "leadership", label: "Leadership & places" },
  { id: "activity", label: "Hiring & activity" },
  { id: "evidence", label: "Evidence" },
  { id: "changes", label: "Changes" },
];

function StatusBadge({ status }: { status: CoverageStatus }) {
  return <span className={`status-badge status-${status.toLowerCase().replace(" ", "-")}`}>{status}</span>;
}

function SourceType({ children }: { children: React.ReactNode }) {
  return <span className="source-type">{children}</span>;
}

function ScoreDots({ score }: { score: number }) {
  return <span className="score-dots" aria-label={`${score} of 5 evidence areas covered`}>{[1, 2, 3, 4, 5].map((dot) => <i key={dot} className={dot <= score ? "is-filled" : ""} />)}</span>;
}

function AppMark() {
  return <span className="app-mark" aria-hidden="true"><i /><i /><i /></span>;
}

export default function Home() {
  const [companies, setCompanies] = useState<Company[]>(demoCompanies);
  const [dataMode, setDataMode] = useState<"loading" | "backend" | "demo">("loading");
  const [backendRun, setBackendRun] = useState("");
  const [query, setQuery] = useState("");
  const [coverageFilter, setCoverageFilter] = useState("all");
  const [sortBy, setSortBy] = useState("complete");
  const [selectedId, setSelectedId] = useState("northstar");
  const [activeSection, setActiveSection] = useState<ProfileSection>("overview");
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [showCompare, setShowCompare] = useState(false);
  const [saved, setSaved] = useState(false);
  const [notice, setNotice] = useState("");
  const [ask, setAsk] = useState("");
  const [askResult, setAskResult] = useState("");
  const [isAsking, setIsAsking] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/companies")
      .then(async (response) => {
        const payload = await response.json() as { companies?: Company[]; run?: { completedAt?: string }; detail?: string };
        if (!response.ok) throw new Error(payload.detail ?? "Backend data is unavailable");
        if (!payload.companies?.length) throw new Error("Backend returned no company profiles");
        return payload;
      })
      .then((payload) => {
        if (cancelled || !payload.companies?.length) return;
        setCompanies(payload.companies);
        setSelectedId(payload.companies[0].id);
        setDataMode("backend");
        setBackendRun(payload.run?.completedAt ?? "Backend refresh complete");
        setNotice(`Loaded ${payload.companies.length} profiles from the backend`);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setDataMode("demo");
        setNotice(error instanceof Error ? `Backend unavailable — showing sample records` : "Backend unavailable — showing sample records");
      });
    return () => { cancelled = true; };
  }, []);

  const selected = companies.find((company) => company.id === selectedId) ?? companies[0];

  const filteredCompanies = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const result = companies.filter((company) => {
      const matchesQuery = !normalizedQuery || [company.name, company.location, company.sector, company.legalName].some((value) => value.toLowerCase().includes(normalizedQuery));
      const matchesCoverage = coverageFilter === "all" || (coverageFilter === "complete" ? company.coverageScore === 5 : company.status === "Needs review");
      return matchesQuery && matchesCoverage;
    });

    return result.sort((a, b) => {
      if (sortBy === "name") return a.name.localeCompare(b.name);
      if (sortBy === "fresh") return a.lastChecked.localeCompare(b.lastChecked);
      return b.coverageScore - a.coverageScore || a.name.localeCompare(b.name);
    });
  }, [companies, coverageFilter, query, sortBy]);

  const compareCompanies = compareIds.map((id) => companies.find((company) => company.id === id)).filter(Boolean) as Company[];

  function selectCompany(id: string) {
    setSelectedId(id);
    setActiveSection("overview");
    setAskResult("");
    const company = companies.find((item) => item.id === id);
    if (company) setNotice(`Viewing ${company.name}`);
  }

  function toggleCompare(id: string) {
    setCompareIds((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length < 3 ? [...current, id] : current);
  }

  function saveView() {
    window.localStorage.setItem("signalpost-saved-view", JSON.stringify({ query, coverageFilter, sortBy, selectedId, savedAt: new Date().toISOString() }));
    setSaved(true);
    setNotice("View saved in this browser");
  }

  function exportProfile() {
    const blob = new Blob([JSON.stringify(selected, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selected.id}-signalpost-profile.json`;
    link.click();
    URL.revokeObjectURL(url);
    setNotice("Profile exported as JSON");
  }

  function answerLocally(prompt: string) {
    if (prompt.includes("what does") || prompt.includes("what do") || prompt.includes("company brief") || prompt.includes("business") || prompt.includes("industry")) {
      return `${selected.summary} Source: ${selected.sources[0]?.name ?? "selected profile evidence"}, checked ${selected.lastChecked}.`;
    } else if (prompt.includes("financial") || prompt.includes("accounts") || prompt.includes("revenue") || prompt.includes("result")) {
      return `${selected.name}'s latest reported period is ${selected.account.period}. Revenue: ${selected.account.revenue}; annual result: ${selected.account.annualResult}; assets: ${selected.account.assets}. Source: ${selected.account.source}.`;
    } else if (prompt.includes("hire") || prompt.includes("job") || prompt.includes("role") || prompt.includes("working here")) {
      return selected.hiring.length ? `${selected.name} has ${selected.hiring.length} hiring signal${selected.hiring.length === 1 ? "" : "s"}. The latest listed role is ${selected.hiring[0].role}, posted ${selected.hiring[0].posted}.` : `No current hiring roles were found for ${selected.name}.`;
    } else if (prompt.includes("leader") || prompt.includes("lead") || prompt.includes("director")) {
      return selected.leaders.length ? `${selected.name} lists ${selected.leaders.map((leader) => `${leader.name} as ${leader.role}`).join(" and ")}.` : `Leadership is not available in the current public record for ${selected.name}.`;
    } else if (prompt.includes("where") || prompt.includes("office") || prompt.includes("location")) {
      return `${selected.name} is registered at ${selected.workplaces[0]?.address ?? "an unavailable address"}. ${selected.workplaces.length > 1 ? `There are ${selected.workplaces.length - 1} additional workplace${selected.workplaces.length > 2 ? "s" : ""} in the record.` : "No additional workplaces are listed."}`;
    } else if (prompt.includes("change") || prompt.includes("recent") || prompt.includes("activity") || prompt.includes("update")) {
      const latestChange = selected.changes[0];
      return latestChange ? `${latestChange.detail} (${latestChange.date}).` : `No material changes were reported for ${selected.name} in the latest backend run.`;
    } else if (prompt.includes("unknown") || prompt.includes("missing") || prompt.includes("gap") || prompt.includes("not available")) {
      const gaps = selected.backend?.unknownAreas ?? [];
      return gaps.length ? `The current public record does not establish ${gaps.map(readableEvidenceArea).join(", ")}. These areas remain explicitly unavailable rather than inferred.` : `No open evidence gaps were reported in the latest backend run.`;
    } else {
      return `${selected.summary} The profile is ${selected.status.toLowerCase()} with ${selected.coverage} evidence coverage. Ask about the company brief, latest financials, leadership, hiring, changes, or remaining unknowns.`;
    }
  }

  async function answerQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = ask.trim();
    if (!question) return;
    const localAnswer = answerLocally(question.toLowerCase());
    setAskResult(localAnswer);
    if (dataMode !== "backend" || !/^\d{9}$/.test(selected.id)) return;
    setIsAsking(true);
    try {
      const response = await fetch(`/api/companies/${encodeURIComponent(selected.id)}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const payload = await response.json() as { data?: { answer?: string }; error?: string };
      if (response.ok && payload.data?.answer) setAskResult(payload.data.answer);
    } catch {
      // The deterministic selected-record answer stays visible if the live answer service is unavailable.
    } finally {
      setIsAsking(false);
    }
  }

  return (
    <div className="app-shell">
        <header className="topbar">
        <a className="brand" href="#home" aria-label="SignalPost home"><AppMark /><span className="brand-name">SignalPost</span><span className="brand-divider" /><span className="brand-context">research desk</span></a>
        <nav className="topnav" aria-label="Primary navigation"><a href="#companies">Companies</a><a href="#evidence">Evidence model</a><a href="#ask">Ask SignalPost</a></nav>
        <div className="topbar-actions"><span className="connection-state"><i className={dataMode === "demo" ? "is-warning" : ""} /> {dataMode === "backend" ? "Backend connected" : dataMode === "loading" ? "Connecting…" : "Demo fallback"}</span><button className="avatar-button" type="button" aria-label="AP account menu">AP</button></div>
      </header>

      <main>
        <section className="hero" id="home">
          <div className="hero-copy"><p className="eyebrow">Company intelligence / signalpost</p><h1>Find the company, then trust the evidence.</h1><p className="hero-lede">A focused research desk for identity, accounts, people, places, activity, and the sources behind every claim.</p><div className="hero-actions"><a className="button button-primary" href="#companies">Search records <span className="button-key">⌘ K</span></a><a className="text-link" href="#evidence">See the evidence model <span aria-hidden="true">↗</span></a></div></div>
          <div className="signal-art" aria-label="Evidence coverage map"><div className="signal-art-header"><span>Live evidence map</span><span className="mono">05 / 05</span></div><div className="signal-orbit orbit-one"><span className="signal-node node-identity" /><span className="signal-node node-accounts" /></div><div className="signal-orbit orbit-two"><span className="signal-node node-leadership" /><span className="signal-node node-activity" /></div><div className="signal-core"><AppMark /><strong>{dataMode === "loading" ? "Connecting…" : selected.name}</strong><span>{dataMode === "loading" ? "Waiting for backend evidence" : `${selected.coverage} evidence areas covered`}</span></div><div className="signal-labels"><span><i className="legend-dot dot-coral" /> public identity</span><span><i className="legend-dot dot-green" /> source checked</span><span><i className="legend-dot dot-ink" /> claim available</span></div></div>
        </section>

        <section className="workspace-section" id="companies">
          <div className="workspace-heading"><div><h2>Company index</h2><p>Search across the records, then inspect one profile without losing the trail.</p></div><div className="index-summary"><strong>{companies.length}</strong><span>profiles loaded</span><span className="summary-rule" /><strong>{dataMode === "backend" ? "LIVE" : dataMode === "loading" ? "…" : "DEMO"}</strong><span>{backendRun || "data mode"}</span></div></div>
          <div className="workspace-grid">
            <aside className="index-panel" aria-label="Company index">
              <div className="index-toolbar"><label className="search-field"><span aria-hidden="true">⌕</span><span className="sr-only">Search company records</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, place, or sector" /><kbd>/</kbd></label><div className="filter-row"><label><span className="sr-only">Filter by evidence coverage</span><select value={coverageFilter} onChange={(event) => setCoverageFilter(event.target.value)}><option value="all">All evidence</option><option value="complete">5/5 coverage</option><option value="review">Needs review</option></select></label><label><span className="sr-only">Sort companies</span><select value={sortBy} onChange={(event) => setSortBy(event.target.value)}><option value="complete">Most complete</option><option value="name">A to Z</option><option value="fresh">Recently checked</option></select></label></div></div>
              <div className="index-count"><span>{filteredCompanies.length} records shown</span><span>{dataMode === "backend" ? "Backend refresh" : dataMode === "loading" ? "Loading backend" : "Sample data"}</span></div>
              <div className="company-list" role="list">
                {filteredCompanies.map((company) => <div className={`company-row ${selectedId === company.id ? "is-selected" : ""}`} key={company.id} role="listitem"><button className="company-select" type="button" onClick={() => selectCompany(company.id)} aria-pressed={selectedId === company.id}><span className="company-initials">{company.name.split(" ").slice(0, 2).map((part) => part[0]).join("")}</span><span className="company-row-copy"><strong>{company.name}</strong><span>{company.location} <span className="row-separator">/</span> {company.sector}</span></span><span className="company-row-meta"><StatusBadge status={company.status} /><span className="coverage-mini"><ScoreDots score={company.coverageScore} /> {company.coverage}</span></span></button><label className="compare-check"><input type="checkbox" checked={compareIds.includes(company.id)} onChange={() => toggleCompare(company.id)} aria-label={`Add ${company.name} to comparison`} /><span aria-hidden="true">+</span></label></div>)}
                {!filteredCompanies.length && <div className="empty-state"><strong>No matching records</strong><p>Try a company name, a location, or remove the coverage filter.</p><button className="text-button" type="button" onClick={() => { setQuery(""); setCoverageFilter("all"); }}>Clear filters</button></div>}
              </div>
              <div className="index-footer"><span className={`legend-dot ${dataMode === "backend" ? "dot-green" : "dot-coral"}`} /> {dataMode === "backend" ? "Evidence loaded from backend artifacts" : "Sample records shown until backend is available"}</div>
            </aside>

            <section className="profile-panel" aria-label={`${selected.name} company profile`}>
              <div className="profile-header"><div><div className="profile-title-row"><span className="verified-mark">✓</span><span className="profile-kicker">Verified company profile</span></div><h2>{selected.name}</h2><p>{selected.location} <span className="row-separator">/</span> {selected.sector}</p></div><div className="profile-actions"><button className="button button-secondary" type="button" onClick={saveView}>{saved ? "Saved" : "Save view"}</button><button className="button button-dark" type="button" onClick={exportProfile}>Export <span aria-hidden="true">↗</span></button></div></div>
              <div className="profile-meta"><span>Last checked <strong>{selected.lastChecked}</strong></span><span>Registration <strong>{selected.registration}</strong></span><a href={`https://${selected.website}`} target="_blank" rel="noreferrer">{selected.website} <span aria-hidden="true">↗</span></a></div>
              <nav className="profile-tabs" aria-label="Company profile sections">{profileSections.map((section) => <button key={section.id} className={activeSection === section.id ? "is-active" : ""} type="button" onClick={() => setActiveSection(section.id)} aria-current={activeSection === section.id ? "page" : undefined}>{section.label}</button>)}</nav>
              {activeSection === "overview" && <Overview company={selected} onOpenEvidence={() => setActiveSection("evidence")} />}
              {activeSection === "financials" && <Financials company={selected} />}
              {activeSection === "leadership" && <Leadership company={selected} />}
              {activeSection === "activity" && <Activity company={selected} />}
              {activeSection === "evidence" && <Evidence company={selected} />}
              {activeSection === "changes" && <Changes company={selected} />}
            </section>
          </div>
        </section>

        {compareCompanies.length >= 2 && <section className="compare-section" aria-label="Company comparison"><div className="compare-header"><div><h2>Compare selected</h2><p>Keep the comparison factual. No ranking is implied.</p></div><button className="button button-secondary" type="button" onClick={() => setShowCompare((current) => !current)}>{showCompare ? "Hide comparison" : `Compare ${compareCompanies.length}`}</button></div>{showCompare && <div className="compare-grid">{compareCompanies.map((company) => <article className="compare-card" key={company.id}><div className="compare-card-top"><span className="company-initials">{company.name.split(" ").slice(0, 2).map((part) => part[0]).join("")}</span><button type="button" className="icon-button" onClick={() => toggleCompare(company.id)} aria-label={`Remove ${company.name} from comparison`}>×</button></div><h3>{company.name}</h3><p>{company.location} <span className="row-separator">/</span> {company.sector}</p><div className="compare-stat"><span>Evidence coverage</span><strong>{company.coverage}</strong></div><div className="compare-stat"><span>Latest accounts</span><strong>{company.account.period.replace("Year ended ", "")}</strong></div><div className="compare-stat"><span>Current hiring</span><strong>{company.hiring.length ? `${company.hiring.length} role${company.hiring.length === 1 ? "" : "s"}` : "None found"}</strong></div><StatusBadge status={company.status} /></article>)}</div>}</section>}

        <section className="ask-section" id="ask"><div className="ask-copy"><p className="eyebrow">Grounded answers</p><h2>Ask about the profile you are looking at.</h2><p>Answers stay inside the selected record and point back to its evidence trail.</p></div><div className="ask-panel"><div className="prompt-row"><button type="button" onClick={() => setAsk("What does this company do?")}>Company brief</button><button type="button" onClick={() => setAsk("What are the latest financials?")}>Latest financials</button><button type="button" onClick={() => setAsk("Who leads the company?")}>Who runs it?</button><button type="button" onClick={() => setAsk("Is the company hiring?")}>Working here</button><button type="button" onClick={() => setAsk("What changed recently?")}>Recent activity</button><button type="button" onClick={() => setAsk("What remains unknown?")}>Open gaps</button></div><form className="ask-form" onSubmit={answerQuestion}><label htmlFor="ask-input">Ask about {dataMode === "backend" ? selected.name : "the selected company"}</label><div className="ask-input-row"><input id="ask-input" value={ask} onChange={(event) => setAsk(event.target.value)} placeholder="Try a question about people, places, or activity" /><button className="button button-primary" type="submit" disabled={isAsking}>{isAsking ? "Checking…" : <>Ask <span aria-hidden="true">↗</span></>}</button></div></form><div className={`answer-box ${askResult ? "has-answer" : ""}`} aria-live="polite">{askResult ? <><span className="answer-label">Based on selected evidence</span><p>{askResult}</p></> : <span>Choose a prompt or ask your own question. The response will use only this profile.</span>}</div></div></section>

        <section className="evidence-strip" id="evidence"><div><h2>Evidence is part of the product.</h2><p>Every record exposes what was found, where it came from, how fresh it is, and where the public record stops.</p></div><div className="evidence-principles"><span><strong>01</strong> Identity first</span><span><strong>02</strong> Claim-level sources</span><span><strong>03</strong> Explicit gaps</span></div></section>
      </main>

      <footer className="footer"><div className="footer-brand"><AppMark /><strong>SignalPost</strong><span>Company intelligence with a visible trail.</span></div><div className="footer-links"><a href="#companies">Companies</a><a href="#evidence">Evidence model</a><a href="#ask">Ask SignalPost</a></div><span className="footer-note">Public-source research prototype</span></footer>
      {notice && <div className="notice" role="status">{notice}<button type="button" onClick={() => setNotice("")} aria-label="Dismiss notice">×</button></div>}
    </div>
  );
}

function Overview({ company, onOpenEvidence }: { company: Company; onOpenEvidence: () => void }) {
  const unknowns = company.backend?.unknownAreas ?? [];
  const latestChange = company.changes[0];

  return <div className="profile-content overview-content"><div className="overview-grid"><article className="brief-card"><div className="card-topline"><span>Company brief</span><SourceType>Claim synthesis</SourceType></div><p className="brief-text">{company.summary}</p><div className="brief-footer"><span>Legal name</span><strong>{company.legalName}</strong></div><div className="brief-footer"><span>Founded</span><strong>{company.founded}</strong></div></article><article className="accounts-card"><div className="card-topline"><span>Latest accounts</span><button className="card-link" type="button" onClick={onOpenEvidence}>View source ↗</button></div><strong className="accounts-year">{company.account.period}</strong><div className="metric-grid"><div><span>Revenue</span><strong>{company.account.revenue}</strong></div><div><span>Annual result</span><strong>{company.account.annualResult}</strong></div><div><span>Assets</span><strong>{company.account.assets}</strong></div><div><span>Debt</span><strong>{company.account.debt}</strong></div></div></article></div><div className="coverage-card"><div className="coverage-card-head"><div><span className="card-eyebrow">Coverage at a glance</span><h3>Five signals, one inspectable profile.</h3></div><button className="text-button" type="button" onClick={onOpenEvidence}>Inspect all evidence <span aria-hidden="true">↗</span></button></div><div className="coverage-grid">{company.availability.map((item) => <div className="coverage-item" key={item.label}><span className={`coverage-icon coverage-${item.tone}`}>{item.tone === "good" ? "✓" : item.tone === "warn" ? "!" : "-"}</span><span><strong>{item.label}</strong><small>{item.value}</small></span></div>)}</div></div><section className="synthesis-card" aria-label="Research synthesis"><div className="synthesis-main"><span className="card-eyebrow">Research synthesis</span><h3>What the checked record supports</h3><p>{company.summary}</p><small>Grounded in {company.sources[0]?.name ?? "the selected backend evidence"}; checked {company.lastChecked}.</small></div><div className="synthesis-side"><div><span className="card-eyebrow">Latest change</span><strong>{latestChange?.label ?? "No change reported"}</strong><p>{latestChange?.detail ?? "No material changes were reported by the latest backend run."}</p><small>{latestChange?.date ?? company.lastChecked}</small></div><div><span className="card-eyebrow">Still unknown</span>{unknowns.length ? <ul>{unknowns.map((area) => <li key={area}>{readableEvidenceArea(area)}</li>)}</ul> : <p>No open evidence gaps were reported.</p>}</div></div></section></div>;
}

function Financials({ company }: { company: Company }) {
  return <div className="profile-content"><div className="section-intro"><div><p className="eyebrow">Financial record</p><h3>Latest annual accounts</h3><p>Numbers are shown exactly as available in the latest public filing.</p></div><SourceType>{company.account.source}</SourceType></div><div className="financial-feature"><div><span>Reporting period</span><strong>{company.account.period}</strong><small>Filed {company.account.filed}</small></div><div><span>Annual result</span><strong>{company.account.annualResult}</strong><small>After tax result</small></div></div><div className="financial-table" role="table" aria-label="Latest annual accounts"><div className="financial-row financial-head" role="row"><span>Metric</span><span>Reported value</span><span>Availability</span></div>{[["Revenue", company.account.revenue], ["Operating result", company.account.operatingResult], ["Annual result", company.account.annualResult], ["Total assets", company.account.assets], ["Debt", company.account.debt]].map(([label, value]) => <div className="financial-row" role="row" key={label}><span>{label}</span><strong>{value}</strong><span className={value === "Not available" || value === "Not reported" ? "missing-value" : "available-value"}>{value === "Not available" || value === "Not reported" ? "Not available" : "Public filing"}</span></div>)}</div><div className="source-footnote"><span className="legend-dot dot-green" /> Source: {company.account.source}. <a className="text-button" href={company.account.sourceUrl ?? "https://data.brreg.no/regnskapsregisteret/"} target="_blank" rel="noreferrer">Open source ↗</a></div></div>;
}

function Leadership({ company }: { company: Company }) {
  return <div className="profile-content"><div className="section-intro"><div><p className="eyebrow">People and places</p><h3>Leadership and registered workplaces</h3><p>Names and locations are separated so each can be checked independently.</p></div><StatusBadge status={company.status} /></div><div className="leadership-grid"><article className="detail-card"><div className="card-topline"><span>Leadership</span><SourceType>Registry record</SourceType></div>{company.leaders.length ? company.leaders.map((leader) => <div className="leader-row" key={leader.name}><span className="person-initials">{leader.name.split(" ").map((part) => part[0]).join("")}</span><span><strong>{leader.name}</strong><small>{leader.role}</small></span><span className="since">Since {leader.since}</span></div>) : <div className="inline-empty"><strong>Leadership not available</strong><span>No public leadership record was returned for this company.</span></div>}</article><article className="detail-card"><div className="card-topline"><span>Registered workplaces</span><SourceType>Registry record</SourceType></div>{company.workplaces.length ? company.workplaces.map((place) => <div className="place-row" key={place.address}><span className="place-index">{place.type === "Registered" ? "R" : "O"}</span><span><strong>{place.label}</strong><small>{place.address}</small></span></div>) : <div className="inline-empty"><strong>Workplaces not available</strong><span>No public workplace record was returned.</span></div>}</article></div><div className="profile-links"><div className="card-topline"><span>Company-owned profiles</span><SourceType>Verified where marked</SourceType></div>{company.profiles.length ? company.profiles.map((profile) => <a href={profile.handle.startsWith("http") ? profile.handle : "https://www.linkedin.com/"} target="_blank" rel="noreferrer" key={`${profile.network}-${profile.handle}`}><span>{profile.network}</span><strong>{profile.handle}</strong><span className={profile.verified ? "profile-verified" : "profile-unverified"}>{profile.verified ? "Verified" : "Unverified"}</span><span aria-hidden="true">↗</span></a>) : <div className="inline-empty"><strong>No company profiles found</strong><span>This evidence area was checked but no public profile was attached.</span></div>}</div></div>;
}

function Activity({ company }: { company: Company }) {
  return <div className="profile-content"><div className="section-intro"><div><p className="eyebrow">Current signals</p><h3>Hiring and public activity</h3><p>Dated signals help separate a live company profile from a static registry record.</p></div><SourceType>Freshness window: 90 days</SourceType></div><div className="activity-grid"><article className="hiring-card"><div className="card-topline"><span>Hiring signals</span><span>{company.hiring.length} listed</span></div>{company.hiring.length ? company.hiring.map((role) => <div className="role-row" key={role.role}><div><strong>{role.role}</strong><span>{role.team}</span></div><div><span>{role.posted}</span><b className={role.status === "Open" ? "role-open" : "role-closed"}>{role.status}</b></div></div>) : <div className="inline-empty"><strong>No current roles found</strong><span>Absence is shown explicitly. It is not treated as a hiring claim.</span></div>}</article><article className="timeline-card"><div className="card-topline"><span>Public activity</span><span>Latest first</span></div>{company.activity.length ? company.activity.map((item) => <div className="timeline-row" key={item.title}><span className="timeline-line" /><div><strong>{item.title}</strong><p>{item.detail}</p><small>{item.date} <span className="row-separator">/</span> {item.source}</small></div></div>) : <div className="inline-empty"><strong>No public activity found</strong><span>This evidence area was checked but no dated public activity was returned.</span></div>}</article></div></div>;
}

function Evidence({ company }: { company: Company }) {
  return <div className="profile-content" id="evidence-detail"><div className="section-intro"><div><p className="eyebrow">Claim-level evidence</p><h3>What the record can support</h3><p>Sources are attached to the claim area they support. Gaps stay visible.</p></div><span className="freshness-pill"><i /> Checked {company.lastChecked}</span></div><div className="evidence-matrix">{company.availability.map((item, index) => <div className="evidence-matrix-row" key={item.label}><span className="evidence-number">0{index + 1}</span><strong>{item.label}</strong><span className={`matrix-status ${item.tone}`}>{item.value}</span><span className="matrix-detail">{item.tone === "good" ? "Source attached" : item.tone === "warn" ? "Review before use" : "No public record found"}</span></div>)}</div><div className="source-list"><div className="card-topline"><span>Sources checked</span><span>{company.sources.length} sources</span></div>{company.sources.length ? company.sources.map((source) => <a href={source.url} target="_blank" rel="noreferrer" className="source-row" key={`${source.name}-${source.url}`}><span className="source-favicon">↗</span><span><strong>{source.name}</strong><small>{source.kind}</small></span><span className="source-date">Checked {source.checked}</span><span aria-hidden="true">↗</span></a>) : <div className="inline-empty"><strong>No source links returned</strong><span>The backend did not attach a public source to this profile.</span></div>}</div></div>;
}

function Changes({ company }: { company: Company }) {
  const [checked, setChecked] = useState(false);
  return <div className="profile-content"><div className="section-intro"><div><p className="eyebrow">Refresh log</p><h3>Material changes and open questions</h3><p>Use this view to see what moved since the previous research pass.</p></div><button className="button button-secondary" type="button" onClick={() => setChecked(true)}>{checked ? "Checked just now" : "Check for changes"}</button></div><div className="changes-list">{company.changes.map((change) => <div className="change-row" key={change.label}><span className={`change-marker ${change.tone}`} /><div><strong>{change.label}</strong><p>{change.detail}</p></div><span className="change-date">{change.date}</span></div>)}</div><div className="abstention-card"><span className="coverage-icon coverage-warn">!</span><div><strong>Research boundary</strong><p>SignalPost reports what a public source supports. It does not infer undisclosed revenue, ownership, hiring plans, or intent.</p></div></div></div>;
}
