"use client";

import { HarnessWorkspace } from "./harness-workspace";

export function ResearchPage() {
  return (
    <section className="settings-page research-page" id="research" aria-label="AI Harness">
      <div className="settings-card">
        <h1>AI Harness</h1>
        <HarnessWorkspace />
      </div>
    </section>
  );
}
