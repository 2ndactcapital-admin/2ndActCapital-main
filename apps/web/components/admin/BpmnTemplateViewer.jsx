"use client";

// WFTEMPLATES1 — a READ-ONLY bpmn-js viewer for a template's diagram (NavigatedViewer: pan / zoom, no editing).
// Kept in its own file so the views in WorkflowTemplates.jsx carry no bpmn-js / CSS import (node render tests).
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";

import { useEffect, useRef, useState } from "react";

export default function BpmnTemplateViewer({ xml }) {
  const ref = useRef(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let viewer;
    let cancelled = false;
    async function init() {
      const { default: NavigatedViewer } = await import("bpmn-js/lib/NavigatedViewer");
      if (cancelled || !ref.current) return;
      viewer = new NavigatedViewer({ container: ref.current });
      try {
        await viewer.importXML(xml || "");
        viewer.get("canvas").zoom("fit-viewport");
      } catch (e) {
        setError(e?.message || "The diagram could not be drawn.");
      }
    }
    init();
    return () => {
      cancelled = true;
      viewer?.destroy();
    };
  }, [xml]);
  return (
    <div>
      <div ref={ref} className="mt-2 h-80 w-full rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6]" />
      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}
    </div>
  );
}
