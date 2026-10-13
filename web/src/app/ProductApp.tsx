import { ArrowLeft, BookOpen, Home, Layers, RefreshCw, Satellite, ShieldCheck } from "lucide-react";
import type { FormEvent } from "react";
import { AppShell, Button, IconButton, SearchPill, Wordmark } from "../kit";
import { useApi, type ChainStatus, type Integrity, type SiteSummary } from "../lib/api";
import { Method } from "./Method";
import { Monitor } from "./Monitor";
import { Overview } from "./Overview";
import { Projects } from "./Projects";
import { Verify } from "./Verify";

export type AppData = {
  sites: ReturnType<typeof useApi<{ sites: SiteSummary[] }>>;
  integrity: ReturnType<typeof useApi<Integrity>>;
  chain: ReturnType<typeof useApi<ChainStatus>>;
};

export const go = (screen: string, id?: string) => {
  location.hash = id ? `#app/${screen}/${encodeURIComponent(id)}` : `#app/${screen}`;
};

const SCREENS = ["overview", "projects", "monitor", "verify", "method"] as const;

export function ProductApp({ hash }: { hash: string }) {
  const [, screenRaw, idRaw] = hash.replace(/^#/, "").split("/");
  const screen = (SCREENS as readonly string[]).includes(screenRaw) ? screenRaw : "overview";
  const id = idRaw ? decodeURIComponent(idRaw) : undefined;

  const data: AppData = {
    sites: useApi<{ sites: SiteSummary[] }>("/api/sites"),
    integrity: useApi<Integrity>("/api/integrity"),
    chain: useApi<ChainStatus>("/api/chain"),
  };
  const reversals = data.integrity.data?.credit_batches.filter((c) => (c.onChain?.status ?? c.status) === "reversal_suspected").length;

  const onSearch = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const q = (e.currentTarget.querySelector("input")?.value ?? "").trim();
    if (!q) return;
    if (q.includes(":")) go("verify", q);
    else {
      const site = data.sites.data?.sites.find((s) => s.site_id.toLowerCase() === q.toLowerCase() || s.name.toLowerCase().includes(q.toLowerCase()));
      go(site ? "monitor" : "verify", site ? site.site_id : q);
    }
  };

  return (
    <AppShell
      brand={
        <a href="#top" aria-label="CanopyProof home">
          <Wordmark name="CANOPY" suffix=".proof" />
        </a>
      }
      nav={[
        {
          items: [
            { id: "overview", label: "Overview", icon: <Home /> },
            { id: "projects", label: "Credit batches", icon: <Layers />, badge: reversals ? String(reversals) : undefined },
            { id: "monitor", label: "Monitoring cells", icon: <Satellite /> },
            { id: "verify", label: "Verify a record", icon: <ShieldCheck /> },
          ],
        },
        {
          section: "Reference",
          items: [
            { id: "method", label: "Method and limits", icon: <BookOpen /> },
            { id: "site", label: "Back to the site", icon: <ArrowLeft /> },
          ],
        },
      ]}
      active={screen}
      onNavigate={(next) => (next === "site" ? (location.hash = "#top") : go(next))}
      greeting="Hello, auditor"
      subtitle="Is the forest behind each credit still standing?"
      actions={
        <>
          <form onSubmit={onSearch} role="search">
            <SearchPill placeholder="Cell or record id" />
          </form>
          <IconButton
            label="Refresh data"
            onClick={() => {
              data.sites.retry();
              data.integrity.retry();
              data.chain.retry();
            }}
          >
            <RefreshCw />
          </IconButton>
          <Button size="sm" onClick={() => go("verify")}>
            Verify a record
          </Button>
        </>
      }
      footer={<ChainFooter chain={data.chain.data} />}
    >
      {screen === "overview" && <Overview data={data} />}
      {screen === "projects" && <Projects data={data} />}
      {screen === "monitor" && <Monitor data={data} siteId={id} />}
      {screen === "verify" && <Verify data={data} recordId={id} />}
      {screen === "method" && <Method data={data} />}
      <MobileTabs screen={screen} />
    </AppShell>
  );
}

function ChainFooter({ chain }: { chain: ChainStatus | null }) {
  const live = chain?.reachable;
  return (
    <div className="px-3 text-[12.5px] leading-relaxed text-muted">
      <p className="flex items-center gap-2 font-display font-semibold text-ink">
        <span className="status-dot" style={{ background: live ? "var(--st-healthy)" : "var(--st-none)" }} />
        {live ? `Chain ${chain?.chainId} live` : "Chain offline"}
      </p>
      <p className="mt-1">
        {live ? `Block ${chain?.blockNumber} · ${chain?.anchoredBatches} batches anchored` : chain?.reason ?? "Start the local node to see live status."}
      </p>
    </div>
  );
}

/** Bottom tab bar replacing the sidebar on small screens. */
function MobileTabs({ screen }: { screen: string }) {
  const tabs = [
    { id: "overview", label: "Overview", icon: <Home size={18} strokeWidth={1.6} /> },
    { id: "projects", label: "Batches", icon: <Layers size={18} strokeWidth={1.6} /> },
    { id: "monitor", label: "Cells", icon: <Satellite size={18} strokeWidth={1.6} /> },
    { id: "verify", label: "Verify", icon: <ShieldCheck size={18} strokeWidth={1.6} /> },
  ];
  return (
    <>
      <div className="h-20 md:hidden" aria-hidden />
      <nav className="glass fixed inset-x-3 bottom-3 z-30 grid grid-cols-4 rounded-[22px] p-1.5 shadow-[var(--shadow-lift)] md:hidden" aria-label="Sections">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => go(t.id)}
            className={`flex flex-col items-center gap-0.5 rounded-2xl py-1.5 font-display text-[11.5px] font-semibold transition-colors ${
              screen === t.id ? "bg-paper-3 text-ink" : "text-ink-2"
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </nav>
    </>
  );
}
