type HostState = "in_sync" | "behind" | "unknown" | "unreachable" | "never_checked";
type Mode = "test" | "switch" | "boot";

interface Host {
  name: string;
  target: string | null;
  deploy: boolean;
  state: HostState;
  flags: string[];
  current_path: string | null;
  current_commit: string | null;
  boot_path: string | null;
  booted_path: string | null;
  expected_path: string | null;
  expected_error: string | null;
  main_sha: string | null;
  uptime_s: number | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  error: string | null;
  active_job: number | null;
}

interface MainInfo {
  branch: string;
  sha: string | null;
  subject: string | null;
  committed_at: string | null;
  fetched_at: string | null;
}

interface Job {
  id: number;
  host: string;
  mode: Mode;
  sha: string;
  subject: string | null;
  state: string;
  exit_code: number | null;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

const TOKEN_KEY = "fleet-deployer-token";
const POLL_MS = 10_000;

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, string> = {},
  ...children: (Node | string | null)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const c of children) if (c !== null) node.append(c);
  return node;
}

function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the token just won't persist */
  }
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ---- formatting ---------------------------------------------------------

function short(sha: string | null): string {
  return sha ? sha.slice(0, 8) : "—";
}

/** "/nix/store/<hash>-nixos-system-vps-26.05.2026…" -> "<hash8> · 26.05.2026…" */
function storeLabel(path: string | null): string {
  if (!path) return "—";
  const base = path.split("/").pop() ?? path;
  const hash = base.slice(0, 8);
  const version = base.match(/-(\d{2}\.\d{2}[^-]*)$/)?.[1];
  return version ? `${hash} · ${version}` : hash;
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function absolute(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "";
}

function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

const STATE_LABEL: Record<HostState, [string, string]> = {
  in_sync: ["In sync", "ok"],
  behind: ["Behind main", "warn"],
  unknown: ["Unknown", "idle"],
  unreachable: ["Unreachable", "bad"],
  never_checked: ["Not checked yet", "idle"],
};

const FLAG_LABEL: Record<string, string> = {
  boot_differs: "boot default differs (test deploy live?)",
  reboot_pending: "reboot pending (new kernel)",
};

const JOB_CLASS: Record<string, string> = {
  queued: "idle",
  running: "warn",
  interrupted: "warn",
  succeeded: "ok",
  failed: "bad",
};

// ---- state --------------------------------------------------------------

let hosts: Host[] = [];
let jobs: Job[] = [];
let mainInfo: MainInfo | null = null;
let selectedJob: number | null = null;
let logOffset = 0;
let logTimer: number | undefined;

function flash(message: string, bad = false): void {
  const node = $("flash");
  node.textContent = message;
  node.className = bad ? "flash bad" : "flash";
  node.hidden = false;
  window.setTimeout(() => (node.hidden = true), 6000);
}

// ---- rendering ----------------------------------------------------------

function renderMain(): void {
  const line = $("main-line");
  line.replaceChildren();
  if (!mainInfo?.sha) {
    line.append("main not fetched yet");
    return;
  }
  line.append(
    `${mainInfo.branch} at `,
    el("code", {}, short(mainInfo.sha)),
    ` · ${mainInfo.subject ?? ""} · committed ${ago(mainInfo.committed_at)} · fetched ${ago(mainInfo.fetched_at)}`,
  );
}

function renderAuth(): void {
  const box = $("auth");
  box.replaceChildren();
  const token = getToken();
  if (token) {
    const forget = el("button", { class: "btn small", type: "button" }, "Forget token");
    forget.addEventListener("click", () => {
      setToken(null);
      renderAll();
    });
    box.append("Token set", forget);
    return;
  }
  const input = el("input", { type: "password", placeholder: "API token", "aria-label": "API token", id: "token" });
  const save = el("button", { class: "btn small primary", type: "button" }, "Unlock deploys");
  const submit = async () => {
    const value = input.value.trim();
    if (!value) return;
    setToken(value);
    try {
      await api("/api/auth");
      renderAll();
    } catch (e) {
      setToken(null);
      flash(`Token rejected: ${(e as Error).message}`, true);
    }
  };
  save.addEventListener("click", submit);
  input.addEventListener("keydown", (ev) => ev.key === "Enter" && submit());
  box.append(input, save);
}

function hostRow(h: Host): HTMLTableRowElement {
  const [label, cls] = STATE_LABEL[h.state];
  const stateCell = el("td", {}, el("span", { class: `pill ${cls}` }, label));
  if (h.flags.length) {
    stateCell.append(el("div", { class: "flags" }, ...h.flags.map((f) => el("span", { class: "flag" }, FLAG_LABEL[f] ?? f))));
  }

  const running = el("td", {}, el("code", { title: h.current_path ?? "" }, storeLabel(h.current_path)));
  if (h.current_commit) running.append(el("span", { class: "detail" }, `commit ${short(h.current_commit)}`));
  else if (h.current_path) running.append(el("span", { class: "detail" }, "not a recent main build"));

  const expected = el("td", {}, el("code", { title: h.expected_path ?? "" }, storeLabel(h.expected_path)));
  if (h.expected_error) expected.append(el("span", { class: "detail err", title: h.expected_error }, "evaluation failed"));

  const checked = el("td", {});
  if (h.state === "unreachable") {
    checked.append(el("span", { title: absolute(h.last_attempt_at) }, `tried ${ago(h.last_attempt_at)}`));
    checked.append(el("span", { class: "detail", title: absolute(h.last_success_at) }, `last seen ${ago(h.last_success_at)}`));
    if (h.error) checked.append(el("span", { class: "detail err" }, h.error));
  } else {
    checked.append(el("span", { title: absolute(h.last_success_at) }, ago(h.last_success_at)));
  }

  const actions = el("div", { class: "actions" });
  if (h.deploy) {
    const locked = !getToken() || h.active_job !== null || !h.main_sha;
    for (const mode of ["test", "switch", "boot"] as Mode[]) {
      const b = el("button", { class: mode === "switch" ? "btn small primary" : "btn small", type: "button" }, mode);
      b.disabled = locked;
      b.title = !getToken() ? "Enter the API token first" : h.active_job !== null ? `Deploy #${h.active_job} in progress` : `nixos-rebuild ${mode}`;
      b.addEventListener("click", () => askDeploy(h, mode));
      actions.append(b);
    }
  } else {
    actions.append(el("span", { class: "detail" }, "status only"));
  }

  return el(
    "tr",
    {},
    el("td", {}, el("span", { class: "host-name" }, h.name), el("span", { class: "host-target" }, h.target ?? "no target")),
    stateCell,
    running,
    expected,
    checked,
    el("td", {}, duration(h.uptime_s)),
    el("td", { class: "right" }, actions),
  );
}

function renderHosts(): void {
  $("hosts").replaceChildren(...hosts.map(hostRow));
}

function renderJobs(): void {
  const list = $("jobs");
  if (!jobs.length) {
    list.replaceChildren(el("li", { class: "empty" }, "No deploys yet"));
    return;
  }
  list.replaceChildren(
    ...jobs.map((j) => {
      const b = el(
        "button",
        { type: "button", "aria-current": String(j.id === selectedJob) },
        el("span", { class: "job-title" }, `#${j.id} ${j.mode} → ${j.host}`),
        el("span", { class: `pill ${JOB_CLASS[j.state] ?? "idle"}` }, j.state),
        el("span", { class: "job-meta" }, `${short(j.sha)} ${j.subject ?? ""} · ${ago(j.started_at ?? j.created_at)}`),
      );
      b.addEventListener("click", () => selectJob(j.id));
      return el("li", {}, b);
    }),
  );
}

function renderAll(): void {
  renderMain();
  renderAuth();
  renderHosts();
  renderJobs();
  ($("refresh") as HTMLButtonElement).disabled = !getToken();
}

// ---- actions ------------------------------------------------------------

function askDeploy(h: Host, mode: Mode): void {
  const box = $("confirm");
  const go = el("button", { class: "btn small primary", type: "button" }, "Deploy");
  const cancel = el("button", { class: "btn small", type: "button" }, "Cancel");
  box.replaceChildren(
    el("span", {}, `nixos-rebuild ${mode} → `, el("strong", {}, h.name), ` at main `, el("code", {}, short(h.main_sha)), "?"),
    go,
    cancel,
  );
  box.hidden = false;
  cancel.addEventListener("click", () => (box.hidden = true));
  go.addEventListener("click", async () => {
    box.hidden = true;
    try {
      const res = await api<{ job: number }>("/api/deploy", {
        method: "POST",
        body: JSON.stringify({ host: h.name, mode, rev: h.main_sha }),
      });
      flash(`Deploy #${res.job} queued`);
      await load();
      selectJob(res.job);
    } catch (e) {
      flash(`Deploy failed to start: ${(e as Error).message}`, true);
    }
  });
  go.focus();
}

async function selectJob(id: number): Promise<void> {
  selectedJob = id;
  logOffset = 0;
  $("log").textContent = "";
  window.clearTimeout(logTimer);
  renderJobs();
  await pollLog();
}

async function pollLog(): Promise<void> {
  if (selectedJob === null) return;
  const id = selectedJob;
  try {
    const res = await api<{ text: string; offset: number; state: string }>(`/api/jobs/${id}/log?offset=${logOffset}`);
    if (id !== selectedJob) return;
    const pre = $("log");
    const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
    pre.append(res.text);
    logOffset = res.offset;
    if (atBottom) pre.scrollTop = pre.scrollHeight;
    const job = jobs.find((j) => j.id === id);
    $("log-head").textContent = `#${id} ${job ? `${job.mode} → ${job.host}` : ""} · ${res.state}`;
    if (["queued", "running", "interrupted"].includes(res.state)) logTimer = window.setTimeout(pollLog, 2000);
  } catch (e) {
    $("log-head").textContent = `#${id}: ${(e as Error).message}`;
  }
}

async function load(): Promise<void> {
  try {
    [hosts, jobs, mainInfo] = await Promise.all([api<Host[]>("/api/hosts"), api<Job[]>("/api/jobs"), api<MainInfo>("/api/main")]);
    renderAll();
  } catch (e) {
    flash(`Could not load status: ${(e as Error).message}`, true);
  }
}

$("refresh").addEventListener("click", async () => {
  try {
    await api("/api/refresh", { method: "POST" });
    flash("Fetching main and polling hosts…");
    window.setTimeout(load, 3000);
  } catch (e) {
    flash(`Refresh failed: ${(e as Error).message}`, true);
  }
});

renderAll();
load();
window.setInterval(load, POLL_MS);
