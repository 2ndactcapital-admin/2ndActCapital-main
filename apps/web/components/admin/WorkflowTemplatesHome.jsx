"use client";

// WFTEMPLATES1 — the client wrappers: each holds the envelope the page fetched server-side and re-reads it through
// the browser proxies after an action (Rule 5: never FastAPI directly from the browser).
import { useState } from "react";
import BpmnTemplateViewer from "@/components/admin/BpmnTemplateViewer";
import { CatalogView, PlatformView, SaveOrgTemplate, TemplateView, send } from "@/components/admin/WorkflowTemplates";

export function TemplatesHome({ initial, workflows }) {
  const [env, setEnv] = useState(initial);
  const reload = async () => setEnv(await send("workflow-templates", ""));
  return (
    <>
      <CatalogView envelope={env} />
      <SaveOrgTemplate envelope={env} workflows={workflows} onSaved={reload} />
    </>
  );
}

export function TemplateHome({ initial }) {
  const [env, setEnv] = useState(initial);
  const reload = async () => setEnv(await send("workflow-templates", `${initial.template.id}?version_id=${initial.version.id}`));
  return <TemplateView envelope={env} Diagram={BpmnTemplateViewer} onAction={reload} />;
}

export function PlatformTemplatesHome({ initial }) {
  const [env, setEnv] = useState(initial);
  const reload = async () => setEnv(await send("platform-workflow-templates", ""));
  return <PlatformView envelope={env} onAction={reload} />;
}
