import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  Bot,
  Braces,
  Check,
  ChevronRight,
  CircleStop,
  Code2,
  Database,
  FileDiff,
  Folder,
  GitBranch,
  History,
  Layers3,
  Loader2,
  Network,
  Play,
  Plus,
  RefreshCcw,
  ServerCog,
  Settings2,
  Sparkles,
  TerminalSquare,
  Trash2,
  Wrench
} from "lucide-react";
import "./styles.css";

const api = {
  async get(path) {
    const response = await fetch(`/api${path}`);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
  async post(path, body) {
    const response = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
  async patch(path, body) {
    const response = await fetch(`/api${path}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
  async delete(path) {
    const response = await fetch(`/api${path}`, { method: "DELETE" });
    if (!response.ok && response.status !== 204) throw new Error(await response.text());
  }
};

const blankPrompt = { name: "", body: "", tags: [] };
const blankEnv = {
  name: "",
  client: "custom",
  model: "",
  baseUrl: "",
  reasoningEffort: "medium",
  commandTemplate: "node {simulator} {promptFile}",
  envVars: {},
  timeoutMs: 600000
};
const blankState = { name: "", path: "", description: "" };

const views = [
  { id: "experiment", label: "Experiment", icon: Activity },
  { id: "prompts", label: "Prompts", icon: Braces },
  { id: "models", label: "Models", icon: Bot },
  { id: "workspace", label: "Workspace", icon: Folder },
  { id: "devtools", label: "DevTools", icon: Wrench }
];

function cx(...parts) {
  return parts.filter(Boolean).join(" ");
}

function formatPayload(payload) {
  return typeof payload === "string" ? payload : JSON.stringify(payload);
}

function parseTranscript(raw) {
  return raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return { id: line, type: "raw", payload: line, ts: new Date().toISOString() };
      }
    });
}

function StatusPill({ status }) {
  return <span className={cx("pill", `pill-${status || "idle"}`)}>{status || "idle"}</span>;
}

function IconButton({ title, children, className, ...props }) {
  return (
    <button className={cx("iconButton", className)} title={title} aria-label={title} {...props}>
      {children}
    </button>
  );
}

function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function TextInput(props) {
  return <input className="input" {...props} />;
}

function TextArea(props) {
  return <textarea className="textarea" {...props} />;
}

function SectionHead({ eyebrow, title, icon: Icon, action }) {
  return (
    <div className="sectionHead">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
      </div>
      {action || (Icon ? <Icon size={22} /> : null)}
    </div>
  );
}

function Dateline() {
  const now = new Date();
  const date = now.toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" });
  const time = now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  return (
    <div className="dateline">
      <strong>The Behavior Ledger</strong>
      {date} · {time} · LOCAL
    </div>
  );
}

function CollectionEditor({ title, icon: Icon, items, activeId, setActiveId, setDraft, onCreate, onDelete, children }) {
  return (
    <section className="panel collectionPanel">
      <div className="panelTitle">
        <Icon size={18} />
        <h2>{title}</h2>
        <IconButton title="Create" onClick={onCreate}>
          <Plus size={16} />
        </IconButton>
      </div>
      <div className="splitEditor">
        <div className="recordList">
          {items.map((item) => (
            <button
              key={item.id}
              className={cx("record", activeId === item.id && "active")}
              onClick={() => {
                setActiveId(item.id);
                setDraft(item);
              }}
            >
              <span>{item.name}</span>
              <ChevronRight size={15} />
            </button>
          ))}
          {!items.length && <div className="emptyLine">No records</div>}
        </div>
        <div className="editorPane">
          {children}
          {activeId && (
            <button className="dangerText" onClick={() => onDelete(activeId)}>
              <Trash2 size={15} />
              Delete
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function ExperimentView({
  store,
  activeEnv,
  setActiveEnv,
  activeState,
  setActiveState,
  runConfig,
  setRunConfig,
  busy,
  start,
  selectedRun,
  setSelectedRunId,
  selectedSession,
  selectedSessionId,
  setSelectedSessionId,
  diff,
  transcript,
  events
}) {
  const sessions = selectedRun?.sessions || [];

  function togglePrompt(promptId) {
    setRunConfig((current) => {
      const exists = current.promptRuns.find((item) => item.promptId === promptId);
      return {
        ...current,
        promptRuns: exists
          ? current.promptRuns.filter((item) => item.promptId !== promptId)
          : [...current.promptRuns, { promptId, count: 1 }]
      };
    });
  }

  return (
    <div className="experimentGrid">
      <section className="controlDeck">
        <SectionHead
          eyebrow="Experiment"
          title="Run Bench"
          action={
            <button className="primary" disabled={busy || !store.states.length || !runConfig.promptRuns.length} onClick={start}>
              {busy ? <Loader2 className="spin" size={17} /> : <Play size={17} />}
              Start
            </button>
          }
        />

        <div className="matrix">
          <div className="choiceBlock">
            <h3>
              <Bot size={17} /> Models
            </h3>
            <div className="envPicker">
              {store.environments.map((env) => (
                <button key={env.id} className={cx("envCard", activeEnv === env.id && "active")} onClick={() => setActiveEnv(env.id)}>
                  <strong>{env.name}</strong>
                  <span>
                    {env.client} / {env.model || "model"}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="choiceBlock">
            <h3>
              <Folder size={17} /> Workspace
            </h3>
            <select className="select" value={activeState || store.states[0]?.id || ""} onChange={(event) => setActiveState(event.target.value)}>
              <option value="">Select a workspace</option>
              {store.states.map((state) => (
                <option key={state.id} value={state.id}>
                  {state.name}
                </option>
              ))}
            </select>
          </div>

          <div className="choiceBlock wide">
            <h3>
              <Layers3 size={17} /> Prompts
            </h3>
            <div className="promptMatrix">
              {store.prompts.map((prompt) => {
                const selected = runConfig.promptRuns.find((item) => item.promptId === prompt.id);
                return (
                  <div key={prompt.id} className={cx("promptRow", selected && "selected")}>
                    <button onClick={() => togglePrompt(prompt.id)}>
                      <span>{selected ? <Check size={15} /> : <Plus size={15} />}</span>
                      {prompt.name}
                    </button>
                    <input
                      type="number"
                      min="1"
                      value={selected?.count || 1}
                      disabled={!selected}
                      onChange={(event) => {
                        const count = Math.max(1, Number(event.target.value || 1));
                        setRunConfig((current) => ({
                          ...current,
                          promptRuns: current.promptRuns.map((item) => (item.promptId === prompt.id ? { ...item, count } : item))
                        }));
                      }}
                    />
                  </div>
                );
              })}
            </div>
          </div>

          <div className="choiceBlock">
            <h3>
              <Settings2 size={17} /> Flow
            </h3>
            <div className="segmented">
              {["serial", "parallel"].map((mode) => (
                <button key={mode} className={runConfig.mode === mode ? "active" : ""} onClick={() => setRunConfig({ ...runConfig, mode })}>
                  {mode}
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="runBoard">
        <SectionHead eyebrow="Runs" title="Execution Ledger" icon={History} />
        <div className="runList">
          {store.runs.map((run) => (
            <button key={run.id} className={cx("runItem", selectedRun?.id === run.id && "active")} onClick={() => setSelectedRunId(run.id)}>
              <div>
                <strong>{run.name}</strong>
                <span>
                  {run.stateName} / {run.environmentName}
                </span>
              </div>
              <StatusPill status={run.status} />
            </button>
          ))}
          {!store.runs.length && <div className="emptyLine tall">No runs yet</div>}
        </div>
      </section>

      <section className="panel sessionsPanel">
        <div className="panelTitle">
          <Activity size={18} />
          <h2>Sessions</h2>
        </div>
        <div className="sessionList">
          {sessions.map((session) => (
            <button
              key={session.id}
              className={cx("sessionItem", selectedSessionId === session.id && "active")}
              onClick={() => setSelectedSessionId(session.id)}
            >
              <span>
                {session.outputStateName || `state-${session.sequence || session.iteration}`} · {session.promptName} #{session.iteration}
              </span>
              <StatusPill status={session.status} />
            </button>
          ))}
          {!sessions.length && <div className="emptyLine">No sessions</div>}
        </div>

        {selectedSession && (
          <div className="sessionDetail">
            <div className="statGrid">
              <span>
                <GitBranch size={14} /> {selectedSession.initialCommit || "none"} {"->"} {selectedSession.finalCommit || "pending"}
              </span>
              <span>
                <CircleStop size={14} /> {selectedSession.exitCode ?? "..."}
              </span>
            </div>
            <div className="changedFiles">
              <span>{selectedSession.inputStateName || "state-i"}</span>
              <span>{selectedSession.outputStateName || "state-i+1"}</span>
            </div>
            <pre className="commandBox">{selectedSession.command}</pre>
            <div className="changedFiles">
              {selectedSession.changedFiles?.map((file) => (
                <span key={`${file.status}-${file.file}`}>
                  {file.status} {file.file}
                </span>
              ))}
              {!selectedSession.changedFiles?.length && <span>No file changes</span>}
            </div>
            <div className="artifactList">
              {(selectedSession.artifacts || []).map((artifact) => (
                <a key={artifact.name} href={`/api/sessions/${selectedSession.id}/artifacts/${encodeURIComponent(artifact.name)}`} target="_blank" rel="noreferrer">
                  {artifact.name} · {artifact.size} B
                </a>
              ))}
              {!selectedSession.artifacts?.length && <span>No artifacts</span>}
            </div>
          </div>
        )}
      </section>

      <section className="panel diffPanel">
        <div className="panelTitle">
          <FileDiff size={18} />
          <h2>Diff</h2>
        </div>
        <pre className="diffBox">{diff || "No diff captured"}</pre>
      </section>

      <section className="panel transcriptPanel">
        <div className="panelTitle">
          <TerminalSquare size={18} />
          <h2>Transcript</h2>
        </div>
        <EventStream events={transcript} empty="No transcript yet" />
      </section>

      <section className="panel eventPanel">
        <div className="panelTitle">
          <Sparkles size={18} />
          <h2>Stream</h2>
        </div>
        <EventStream events={events} empty="Waiting" />
      </section>
    </div>
  );
}

function EventStream({ events, empty }) {
  return (
    <div className="eventStream">
      {events.map((event) => (
        <div key={event.id} className={cx("eventLine", event.type)}>
          <time>{new Date(event.ts).toLocaleTimeString()}</time>
          <strong>{event.type}</strong>
          <span>{formatPayload(event.payload)}</span>
        </div>
      ))}
      {!events.length && <div className="emptyLine">{empty}</div>}
    </div>
  );
}

function PromptsView({ store, activePrompt, setActivePrompt, promptDraft, setPromptDraft, save, remove, busy }) {
  return (
    <CollectionEditor
      title="Prompts"
      icon={Braces}
      items={store.prompts}
      activeId={activePrompt}
      setActiveId={setActivePrompt}
      setDraft={setPromptDraft}
      onCreate={() => {
        setActivePrompt("");
        setPromptDraft(blankPrompt);
      }}
      onDelete={(id) => remove("prompts", id, setActivePrompt)}
    >
      <Field label="Name">
        <TextInput value={promptDraft.name} onChange={(event) => setPromptDraft({ ...promptDraft, name: event.target.value })} />
      </Field>
      <Field label="Prompt">
        <TextArea rows={13} value={promptDraft.body} onChange={(event) => setPromptDraft({ ...promptDraft, body: event.target.value })} />
      </Field>
      <button className="primary mini" disabled={busy || !promptDraft.name || !promptDraft.body} onClick={() => save("prompts", activePrompt, promptDraft, setActivePrompt)}>
        <Check size={16} />
        Save Prompt
      </button>
    </CollectionEditor>
  );
}

function ModelsView({ store, activeEnv, setActiveEnv, envDraft, setEnvDraft, save, remove, busy }) {
  return (
    <CollectionEditor
      title="Models"
      icon={Bot}
      items={store.environments}
      activeId={activeEnv}
      setActiveId={setActiveEnv}
      setDraft={setEnvDraft}
      onCreate={() => {
        setActiveEnv("");
        setEnvDraft(blankEnv);
      }}
      onDelete={(id) => remove("environments", id, setActiveEnv)}
    >
      <div className="formGrid">
        <Field label="Name">
          <TextInput value={envDraft.name} onChange={(event) => setEnvDraft({ ...envDraft, name: event.target.value })} />
        </Field>
        <Field label="Client">
          <select className="select" value={envDraft.client} onChange={(event) => setEnvDraft({ ...envDraft, client: event.target.value })}>
            <option value="codex">codex</option>
            <option value="claude">claude</option>
            <option value="custom">custom</option>
          </select>
        </Field>
        <Field label="Model">
          <TextInput value={envDraft.model} onChange={(event) => setEnvDraft({ ...envDraft, model: event.target.value })} />
        </Field>
        <Field label="Reasoning">
          <TextInput value={envDraft.reasoningEffort} onChange={(event) => setEnvDraft({ ...envDraft, reasoningEffort: event.target.value })} />
        </Field>
        <Field label="Timeout ms">
          <TextInput type="number" value={envDraft.timeoutMs} onChange={(event) => setEnvDraft({ ...envDraft, timeoutMs: Number(event.target.value) })} />
        </Field>
      </div>
      <Field label="Command">
        <TextArea rows={4} value={envDraft.commandTemplate} onChange={(event) => setEnvDraft({ ...envDraft, commandTemplate: event.target.value })} />
      </Field>
      <button className="primary mini" disabled={busy || !envDraft.name || !envDraft.commandTemplate} onClick={() => save("environments", activeEnv, envDraft, setActiveEnv)}>
        <Bot size={16} />
        Save Model
      </button>
    </CollectionEditor>
  );
}

function WorkspaceView({ store, activeState, setActiveState, stateDraft, setStateDraft, save, remove, busy }) {
  return (
    <CollectionEditor
      title="Workspace"
      icon={Folder}
      items={store.states}
      activeId={activeState}
      setActiveId={setActiveState}
      setDraft={setStateDraft}
      onCreate={() => {
        setActiveState("");
        setStateDraft(blankState);
      }}
      onDelete={(id) => remove("states", id, setActiveState)}
    >
      <Field label="Name">
        <TextInput value={stateDraft.name} onChange={(event) => setStateDraft({ ...stateDraft, name: event.target.value })} />
      </Field>
      <Field label="Folder">
        <TextInput placeholder="/absolute/path/to/workspace" value={stateDraft.path} onChange={(event) => setStateDraft({ ...stateDraft, path: event.target.value })} />
      </Field>
      <Field label="Notes">
        <TextArea rows={5} value={stateDraft.description} onChange={(event) => setStateDraft({ ...stateDraft, description: event.target.value })} />
      </Field>
      <button className="primary mini" disabled={busy || !stateDraft.name || !stateDraft.path} onClick={() => save("states", activeState, stateDraft, setActiveState)}>
        <Database size={16} />
        Save Workspace
      </button>
    </CollectionEditor>
  );
}

function DevToolsView({ store, activeEnv, setActiveEnv, envDraft, setEnvDraft, save, events, busy }) {
  const endpoints = [
    "GET /api/store",
    "POST /api/runs",
    "GET /api/events",
    "POST /api/agent/events",
    "POST /api/agent/artifacts"
  ];

  return (
    <div className="devtoolsGrid">
      <section className="panel">
        <SectionHead eyebrow="Endpoint" title="Base URL" icon={Network} />
        <Field label="Model">
          <select
            className="select"
            value={activeEnv}
            onChange={(event) => {
              const env = store.environments.find((item) => item.id === event.target.value);
              setActiveEnv(event.target.value);
              setEnvDraft(env || blankEnv);
            }}
          >
            <option value="">New model</option>
            {store.environments.map((env) => (
              <option key={env.id} value={env.id}>
                {env.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Base URL">
          <TextInput value={envDraft.baseUrl} onChange={(event) => setEnvDraft({ ...envDraft, baseUrl: event.target.value })} />
        </Field>
        <button className="primary mini" disabled={busy || !envDraft.name} onClick={() => save("environments", activeEnv, envDraft, setActiveEnv)}>
          <ServerCog size={16} />
          Save Base URL
        </button>
      </section>

      <section className="panel">
        <SectionHead eyebrow="API" title="Local Surface" icon={Code2} />
        <div className="apiList">
          {endpoints.map((endpoint) => (
            <code key={endpoint}>{endpoint}</code>
          ))}
        </div>
      </section>

      <section className="panel eventPanel">
        <div className="panelTitle">
          <Sparkles size={18} />
          <h2>Events</h2>
        </div>
        <EventStream events={events} empty="Waiting" />
      </section>
    </div>
  );
}

function App() {
  const [store, setStore] = useState({ prompts: [], environments: [], states: [], runs: [] });
  const initializedRef = useRef(false);
  const [activeView, setActiveView] = useState("experiment");
  const [promptDraft, setPromptDraft] = useState(blankPrompt);
  const [envDraft, setEnvDraft] = useState(blankEnv);
  const [stateDraft, setStateDraft] = useState(blankState);
  const [activePrompt, setActivePrompt] = useState("");
  const [activeEnv, setActiveEnv] = useState("");
  const [activeState, setActiveState] = useState("");
  const [runConfig, setRunConfig] = useState({ mode: "serial", promptRuns: [] });
  const [selectedRunId, setSelectedRunId] = useState("");
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [diff, setDiff] = useState("");
  const [transcript, setTranscript] = useState([]);
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const previousActivePromptRef = useRef("");
  const previousActiveEnvRef = useRef("");
  const previousActiveStateRef = useRef("");

  async function refresh() {
    const next = await api.get("/store");
    const promptIds = new Set(next.prompts.map((prompt) => prompt.id));
    setStore(next);
    setRunConfig((current) => {
      const promptRuns = current.promptRuns.filter((item) => promptIds.has(item.promptId));
      return promptRuns.length === current.promptRuns.length ? current : { ...current, promptRuns };
    });
    setActivePrompt((current) => (current && !promptIds.has(current) ? "" : current));
    if (!initializedRef.current) {
      initializedRef.current = true;
      setActiveEnv(next.environments[0]?.id || "");
      setActiveState(next.states[0]?.id || "");
      setActivePrompt(next.prompts[0]?.id || "");
      setPromptDraft(next.prompts[0] || blankPrompt);
      setEnvDraft(next.environments[0] || blankEnv);
      setStateDraft(next.states[0] || blankState);
      setRunConfig((current) => ({
        ...current,
        promptRuns: next.prompts.slice(0, 1).map((prompt) => ({ promptId: prompt.id, count: 1 }))
      }));
      setSelectedRunId(next.runs[0]?.id || "");
    }
  }

  useEffect(() => {
    refresh().catch((err) => setError(err.message));
    const timer = setInterval(() => refresh().catch(() => {}), 2500);
    const source = new EventSource("/api/events");
    source.onmessage = (event) => setEvents((current) => [JSON.parse(event.data), ...current].slice(0, 80));
    for (const type of ["stdout", "stderr", "error", "session_started", "session_finished", "run_finished", "run_failed"]) {
      source.addEventListener(type, (event) => setEvents((current) => [JSON.parse(event.data), ...current].slice(0, 80)));
    }
    return () => {
      clearInterval(timer);
      source.close();
    };
  }, []);

  useEffect(() => {
    if (previousActiveEnvRef.current === activeEnv) return;
    previousActiveEnvRef.current = activeEnv;
    if (!activeEnv) {
      setEnvDraft(blankEnv);
      return;
    }
    const env = store.environments.find((item) => item.id === activeEnv);
    if (env) setEnvDraft(env);
  }, [activeEnv, store.environments]);

  useEffect(() => {
    if (previousActivePromptRef.current === activePrompt) return;
    previousActivePromptRef.current = activePrompt;
    if (!activePrompt) {
      setPromptDraft(blankPrompt);
      return;
    }
    const prompt = store.prompts.find((item) => item.id === activePrompt);
    if (prompt) setPromptDraft(prompt);
  }, [activePrompt, store.prompts]);

  useEffect(() => {
    if (previousActiveStateRef.current === activeState) return;
    previousActiveStateRef.current = activeState;
    if (!activeState) {
      setStateDraft(blankState);
      return;
    }
    const state = store.states.find((item) => item.id === activeState);
    if (state) setStateDraft(state);
  }, [activeState, store.states]);

  const selectedRun = useMemo(() => store.runs.find((run) => run.id === selectedRunId) || store.runs[0], [store.runs, selectedRunId]);
  const selectedSession = useMemo(
    () => selectedRun?.sessions.find((session) => session.id === selectedSessionId) || selectedRun?.sessions[0],
    [selectedRun, selectedSessionId]
  );

  useEffect(() => {
    if (!selectedSession) {
      setDiff("");
      setTranscript([]);
      return;
    }
    fetch(`/api/sessions/${selectedSession.id}/diff`)
      .then((response) => response.text())
      .then(setDiff)
      .catch(() => setDiff(""));
    fetch(`/api/sessions/${selectedSession.id}/transcript`)
      .then((response) => response.text())
      .then((text) => setTranscript(parseTranscript(text)))
      .catch(() => setTranscript([]));
  }, [selectedSession?.id]);

  async function save(collection, activeId, draft, setter) {
    setBusy(true);
    setError("");
    try {
      const item = activeId ? await api.patch(`/${collection}/${activeId}`, draft) : await api.post(`/${collection}`, draft);
      setter(item.id);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(collection, id, clear) {
    setBusy(true);
    setError("");
    try {
      await api.delete(`/${collection}/${id}`);
      clear("");
      if (collection === "prompts") {
        setPromptDraft(blankPrompt);
        setRunConfig((current) => ({
          ...current,
          promptRuns: current.promptRuns.filter((item) => item.promptId !== id)
        }));
      }
      if (collection === "environments") setEnvDraft(blankEnv);
      if (collection === "states") setStateDraft(blankState);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setBusy(true);
    setError("");
    try {
      const run = await api.post("/runs", {
        stateId: activeState || store.states[0]?.id,
        environmentId: activeEnv || store.environments[0]?.id,
        mode: runConfig.mode,
        promptRuns: runConfig.promptRuns
      });
      setSelectedRunId(run.id);
      setSelectedSessionId("");
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const running = store.runs.some((run) => run.status === "running");
  const activeViewMeta = views.find((view) => view.id === activeView);

  return (
    <main className="appShell">
      <header className="topbar">
        <div className="brand">
          <img src="/mark.svg" alt="" />
          <div>
            <h1>
              Status <em>Machine</em>
            </h1>
            <span>{activeViewMeta?.label || "Experiment"} · Behavior Bench</span>
          </div>
        </div>
        <Dateline />
        <div className="topActions">
          <StatusPill status={running ? "running" : "ready"} />
          <IconButton title="Refresh" onClick={() => refresh()}>
            <RefreshCcw size={17} />
          </IconButton>
        </div>
      </header>

      <nav className="viewTabs" aria-label="Primary">
        {views.map(({ id, label, icon: Icon }) => (
          <button key={id} className={cx(activeView === id && "active")} onClick={() => setActiveView(id)}>
            <Icon size={17} />
            {label}
          </button>
        ))}
      </nav>

      {error && <div className="toast">{error}</div>}

      <div className="viewFrame">
        {activeView === "experiment" && (
          <ExperimentView
            store={store}
            activeEnv={activeEnv}
            setActiveEnv={setActiveEnv}
            activeState={activeState}
            setActiveState={setActiveState}
            runConfig={runConfig}
            setRunConfig={setRunConfig}
            busy={busy}
            start={start}
            selectedRun={selectedRun}
            setSelectedRunId={setSelectedRunId}
            selectedSession={selectedSession}
            selectedSessionId={selectedSessionId || selectedSession?.id || ""}
            setSelectedSessionId={setSelectedSessionId}
            diff={diff}
            transcript={transcript}
            events={events}
          />
        )}
        {activeView === "prompts" && (
          <PromptsView
            store={store}
            activePrompt={activePrompt}
            setActivePrompt={setActivePrompt}
            promptDraft={promptDraft}
            setPromptDraft={setPromptDraft}
            save={save}
            remove={remove}
            busy={busy}
          />
        )}
        {activeView === "models" && (
          <ModelsView
            store={store}
            activeEnv={activeEnv}
            setActiveEnv={setActiveEnv}
            envDraft={envDraft}
            setEnvDraft={setEnvDraft}
            save={save}
            remove={remove}
            busy={busy}
          />
        )}
        {activeView === "workspace" && (
          <WorkspaceView
            store={store}
            activeState={activeState}
            setActiveState={setActiveState}
            stateDraft={stateDraft}
            setStateDraft={setStateDraft}
            save={save}
            remove={remove}
            busy={busy}
          />
        )}
        {activeView === "devtools" && (
          <DevToolsView
            store={store}
            activeEnv={activeEnv}
            setActiveEnv={setActiveEnv}
            envDraft={envDraft}
            setEnvDraft={setEnvDraft}
            save={save}
            events={events}
            busy={busy}
          />
        )}
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
