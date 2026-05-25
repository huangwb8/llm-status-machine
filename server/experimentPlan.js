export function createExecutionPlan({ mode = "serial", runId, initialWorkspace, promptRuns = [], prompts = [], outputWorkspaceFor }) {
  const promptById = new Map(prompts.map((prompt) => [prompt.id, prompt]));
  const parallel = mode === "parallel";
  const outputPath =
    outputWorkspaceFor ||
    (({ outputLabel }) => {
      return `${runId}/${outputLabel}/workspace`;
    });

  const jobs = [];
  let previousWorkspace = initialWorkspace;
  let sequence = 0;

  for (const selection of promptRuns) {
    const prompt = promptById.get(selection.promptId);
    if (!prompt) continue;

    const count = Math.max(1, Number(selection.count || 1));
    for (let iteration = 1; iteration <= count; iteration += 1) {
      sequence += 1;
      const outputLabel = `state-${sequence}`;
      const outputWorkspace = outputPath({ runId, outputLabel, sequence, prompt, iteration });
      const sourceWorkspace = parallel ? initialWorkspace : previousWorkspace;

      jobs.push({
        prompt,
        iteration,
        sequence,
        sourceWorkspace,
        outputWorkspace,
        outputLabel
      });

      if (!parallel) previousWorkspace = outputWorkspace;
    }
  }

  return jobs;
}
