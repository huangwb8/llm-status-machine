import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  Bot,
  Boxes,
  Braces,
  Check,
  ChevronRight,
  CircleStop,
  FileDiff,
  Folder,
  GitBranch,
  History,
  Layers3,
  Loader2,
  Play,
  Plus,
  RefreshCcw,
  Settings2,
  Sparkles,
  TerminalSquare,
  Trash2
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

const blankPrompt = {
  name: "",
  body: "",
  tags: []
};

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

const blankState = {
  name: "",
  path: "",
  description: ""
};

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

function CollectionEditor({ title, icon: Icon, items, activeId, setActiveId, draft, setDraft, onCreate, onDelete, children }) {
  return (
    <section className="panel">
      <div className="panelTitle">
        <Icon size={18} />
        <h2>{title}</h2>
        <IconButton title="Create" onClick={onCreate}>
          <Plus size={16} />
        </IconButton>
      </div>
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
    </section>
  );
}

function App() {
  const [store, setStore] = useState({ prompts: [], environments: [], states: [], runs: [] });
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

  async function refresh() {
    const next = await api.get("/store");
    setStore(next);
    setActiveEnv((current) => current || next.environments[0]?.id || "");
    setRunConfig((current) => ({
      ...current,
      promptRuns: current.promptRuns.length
        ? current.promptRuns
        : next.prompts.slice(0, 1).map((prompt) => ({ promptId: prompt.id, count: 1 }))
    }));
    setSelectedRunId((current) => current || next.runs[0]?.id || "");
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
    const env = store.environments.find((item) => item.id === activeEnv);
    if (env) setEnvDraft(env);
  }, [activeEnv, store.environments]);

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
    try {
      await api.delete(`/${collection}/${id}`);
      clear("");
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

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
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const sessions = selectedRun?.sessions || [];
  const running = store.runs.some((run) => run.status === "running");
  const selectedBranch = selectedSession?.branch || "main";

  return (
    <main className="appShell">
      <header className="topbar">
        <div className="brand">
          <img src="/mark.svg" alt="" />
          <div>
            <h1>LLM Status Machine</h1>
            <span>Prompt lab / local snapshots / behavior trails</span>
          </div>
        </div>
        <div className="topActions">
          <StatusPill status={running ? "running" : "ready"} />
          <IconButton title="Refresh" onClick={() => refresh()}>
            <RefreshCcw size={17} />
          </IconButton>
        </div>
      </header>

      {error && <div className="toast">{error}</div>}

      <div className="workspaceGrid">
        <aside className="leftRail">
          <CollectionEditor
            title="Prompts"
            icon={Braces}
            items={store.prompts}
            activeId={activePrompt}
            setActiveId={setActivePrompt}
            draft={promptDraft}
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
              <TextArea rows={7} value={promptDraft.body} onChange={(event) => setPromptDraft({ ...promptDraft, body: event.target.value })} />
            </Field>
            <button className="primary mini" disabled={busy || !promptDraft.name || !promptDraft.body} onClick={() => save("prompts", activePrompt, promptDraft, setActivePrompt)}>
              <Check size={16} />
              Save Prompt
            </button>
          </CollectionEditor>

          <CollectionEditor
            title="States"
            icon={Folder}
            items={store.states}
            activeId={activeState}
            setActiveId={setActiveState}
            draft={stateDraft}
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
              <TextArea rows={3} value={stateDraft.description} onChange={(event) => setStateDraft({ ...stateDraft, description: event.target.value })} />
            </Field>
            <button className="primary mini" disabled={busy || !stateDraft.name || !stateDraft.path} onClick={() => save("states", activeState, stateDraft, setActiveState)}>
              <Check size={16} />
              Save State
            </button>
          </CollectionEditor>
        </aside>

        <section className="centerStage">
          <section className="controlDeck">
            <div className="sectionHead">
              <div>
                <span className="eyebrow">Run builder</span>
                <h2>Experiment Matrix</h2>
              </div>
              <button className="primary" disabled={busy || !store.states.length || !runConfig.promptRuns.length} onClick={start}>
                {busy ? <Loader2 className="spin" size={17} /> : <Play size={17} />}
                Start
              </button>
            </div>

            <div className="matrix">
              <div className="choiceBlock">
                <h3><TerminalSquare size={17} /> Environment</h3>
                <div className="envPicker">
                  {store.environments.map((env) => (
                    <button key={env.id} className={cx("envCard", activeEnv === env.id && "active")} onClick={() => setActiveEnv(env.id)}>
                      <strong>{env.name}</strong>
                      <span>{env.client} / {env.model || "model"}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="choiceBlock">
                <h3><Folder size={17} /> State</h3>
                <select className="select" value={activeState || store.states[0]?.id || ""} onChange={(event) => setActiveState(event.target.value)}>
                  <option value="">Select a state</option>
                  {store.states.map((state) => (
                    <option key={state.id} value={state.id}>{state.name}</option>
                  ))}
                </select>
              </div>

              <div className="choiceBlock wide">
                <h3><Layers3 size={17} /> Prompts</h3>
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
                            const count = Number(event.target.value);
                            setRunConfig((current) => ({
                              ...current,
                              promptRuns: current.promptRuns.map((item) => item.promptId === prompt.id ? { ...item, count } : item)
                            }));
                          }}
                        />
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="choiceBlock">
                <h3><Boxes size={17} /> Mode</h3>
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
            <div className="sectionHead compact">
              <div>
                <span className="eyebrow">Runs</span>
                <h2>Execution Ledger</h2>
              </div>
              <History size={20} />
            </div>

            <div className="runList">
              {store.runs.map((run) => (
                <button key={run.id} className={cx("runItem", selectedRun?.id === run.id && "active")} onClick={() => setSelectedRunId(run.id)}>
                  <div>
                    <strong>{run.name}</strong>
                    <span>{run.stateName} / {run.environmentName}</span>
                  </div>
                  <StatusPill status={run.status} />
                </button>
              ))}
              {!store.runs.length && <div className="emptyLine tall">No runs yet</div>}
            </div>
          </section>
        </section>

        <aside className="rightRail">
          <section className="panel tallPanel">
            <div className="panelTitle">
              <Activity size={18} />
              <h2>Sessions</h2>
            </div>
            <div className="sessionList">
              {sessions.map((session) => (
                <button
                  key={session.id}
                  className={cx("sessionItem", selectedSession?.id === session.id && "active")}
                  onClick={() => setSelectedSessionId(session.id)}
                >
                  <span>{session.promptName} #{session.iteration}</span>
                  <StatusPill status={session.status} />
                </button>
              ))}
              {!sessions.length && <div className="emptyLine">No sessions</div>}
            </div>

            {selectedSession && (
              <div className="sessionDetail">
                <div className="statGrid">
                  <span><GitBranch size={14} /> {selectedBranch}: {selectedSession.initialCommit || "none"} {"->"} {selectedSession.finalCommit || "pending"}</span>
                  <span><CircleStop size={14} /> {selectedSession.exitCode ?? "..."}</span>
                </div>
                <pre className="commandBox">{selectedSession.command}</pre>
                <div className="changedFiles">
                  {selectedSession.changedFiles?.map((file) => (
                    <span key={`${file.status}-${file.file}`}>{file.status} {file.file}</span>
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
            <div className="transcriptStream">
              {transcript.map((event) => (
                <div key={event.id} className={cx("eventLine", event.type)}>
                  <time>{new Date(event.ts).toLocaleTimeString()}</time>
                  <strong>{event.type}</strong>
                  <span>{formatPayload(event.payload)}</span>
                </div>
              ))}
              {!transcript.length && <div className="emptyLine">No transcript yet</div>}
            </div>
          </section>

          <section className="panel eventPanel">
            <div className="panelTitle">
              <Sparkles size={18} />
              <h2>Stream</h2>
            </div>
            <div className="eventStream">
              {events.map((event) => (
                <div key={event.id} className={cx("eventLine", event.type)}>
                  <time>{new Date(event.ts).toLocaleTimeString()}</time>
                  <strong>{event.type}</strong>
                  <span>{formatPayload(event.payload)}</span>
                </div>
              ))}
              {!events.length && <div className="emptyLine">Waiting</div>}
            </div>
          </section>
        </aside>
      </div>

      <section className="environmentDock">
        <div className="dockTitle">
          <Settings2 size={18} />
          <h2>Environment Editor</h2>
        </div>
        <div className="envForm">
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
          <Field label="Base URL">
            <TextInput value={envDraft.baseUrl} onChange={(event) => setEnvDraft({ ...envDraft, baseUrl: event.target.value })} />
          </Field>
          <Field label="Reasoning">
            <TextInput value={envDraft.reasoningEffort} onChange={(event) => setEnvDraft({ ...envDraft, reasoningEffort: event.target.value })} />
          </Field>
          <Field label="Timeout ms">
            <TextInput type="number" value={envDraft.timeoutMs} onChange={(event) => setEnvDraft({ ...envDraft, timeoutMs: Number(event.target.value) })} />
          </Field>
          <Field label="Command">
            <TextArea rows={2} value={envDraft.commandTemplate} onChange={(event) => setEnvDraft({ ...envDraft, commandTemplate: event.target.value })} />
          </Field>
          <div className="dockActions">
            <select
              className="select"
              value={activeEnv}
              onChange={(event) => {
                const env = store.environments.find((item) => item.id === event.target.value);
                setActiveEnv(event.target.value);
                setEnvDraft(env || blankEnv);
              }}
            >
              <option value="">New environment</option>
              {store.environments.map((env) => (
                <option key={env.id} value={env.id}>{env.name}</option>
              ))}
            </select>
            <button className="primary mini" disabled={busy || !envDraft.name || !envDraft.commandTemplate} onClick={() => save("environments", activeEnv, envDraft, setActiveEnv)}>
              <Bot size={16} />
              Save Environment
            </button>
            {activeEnv && (
              <IconButton title="Delete environment" onClick={() => remove("environments", activeEnv, setActiveEnv)}>
                <Trash2 size={16} />
              </IconButton>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
