"use client";

import { useEffect, useRef, useState } from "react";
import { saveWorkflowVersionAction } from "@/lib/workflowActions";

// bpmn-js + properties-panel styles. CSS imports are collected by the bundler
// (never executed during SSR), so they are safe in a client component. All
// bpmn-js JavaScript is imported dynamically inside the effect below because it
// touches `window`/`document` at module scope and must not run on the server.
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";
import "@bpmn-io/properties-panel/dist/assets/properties-panel.css";

// Custom governance namespace. Mirrors services/workflow_steps_deriver.py:
// governance lives in <bpmn:extensionElements><twoa:governance .../></…>. The
// `tagAlias: "lowerCase"` makes the JS type `Governance` serialize to the
// lowercase XML tag `governance` the deriver reads — and parse the existing
// stored XML back into the same type on load.
const EXT_URI = "http://2ndactcapital.com/bpmn/ext";
const GOVERNED_TYPES = [
  "bpmn:ServiceTask",
  "bpmn:UserTask",
  "bpmn:SendTask",
  "bpmn:BusinessRuleTask",
];

const twoaModdle = {
  name: "TwoAExtension",
  uri: EXT_URI,
  prefix: "twoa",
  xml: { tagAlias: "lowerCase" },
  associations: [],
  types: [
    {
      name: "Governance",
      superClass: ["Element"],
      properties: [
        { name: "actionRegistryKey", isAttr: true, type: "String" },
        { name: "assignedRoleProfileId", isAttr: true, type: "String" },
        { name: "autonomyTier", isAttr: true, type: "String" },
      ],
    },
  ],
};

function getGovernance(element) {
  const bo = element.businessObject;
  const ext = bo && bo.extensionElements;
  if (!ext || !ext.values) return null;
  return ext.values.find((v) => v.$type === "twoa:Governance") || null;
}

function updateGovernance(element, props, { modeling, bpmnFactory }) {
  const bo = element.businessObject;
  let ext = bo.extensionElements;
  if (!ext) {
    ext = bpmnFactory.create("bpmn:ExtensionElements", { values: [] });
    ext.$parent = bo;
    modeling.updateProperties(element, { extensionElements: ext });
  }
  let gov = (ext.values || []).find((v) => v.$type === "twoa:Governance");
  if (!gov) {
    gov = bpmnFactory.create("twoa:Governance", {});
    gov.$parent = ext;
    modeling.updateModdleProperties(element, ext, {
      values: [...(ext.values || []), gov],
    });
  }
  modeling.updateModdleProperties(element, gov, props);
}

// Build a didi module that adds one "Governance" group to the properties panel
// for every actionable element. `panel` = @bpmn-io/properties-panel exports,
// `pp` = bpmn-js-properties-panel exports. `profiles`/`actions`/`defaults` are
// closed over so the pickers show only real rows + the computed tier default.
function createGovernanceModule({ panel, pp, profiles, actions, defaults }) {
  const { Group, SelectEntry, isSelectEntryEdited } = panel;
  const { useService } = pp;

  function services() {
    return {
      modeling: useService("modeling"),
      bpmnFactory: useService("bpmnFactory"),
    };
  }

  function ActionKeyEntry(props) {
    const { element } = props;
    const svc = services();
    return SelectEntry({
      id: "twoa-action-registry-key",
      element,
      label: "Action (Service Task)",
      getValue: () => getGovernance(element)?.actionRegistryKey || "",
      setValue: (value) =>
        updateGovernance(element, { actionRegistryKey: value || undefined }, svc),
      getOptions: () => [
        { value: "", label: "— none —" },
        ...actions.map((a) => ({
          value: a.key,
          label: `${a.key} (${a.access_type})`,
        })),
      ],
    });
  }

  function ProfileEntry(props) {
    const { element } = props;
    const svc = services();
    return SelectEntry({
      id: "twoa-assigned-role-profile-id",
      element,
      label: "Assigned role (Profile)",
      getValue: () => getGovernance(element)?.assignedRoleProfileId || "",
      setValue: (value) =>
        updateGovernance(
          element,
          { assignedRoleProfileId: value || undefined },
          svc,
        ),
      getOptions: () => [
        { value: "", label: "— unassigned —" },
        ...profiles.map((p) => ({ value: p.id, label: p.name })),
      ],
    });
  }

  function TierEntry(props) {
    const { element } = props;
    const svc = services();
    const fallback = defaults[element.id];
    return SelectEntry({
      id: "twoa-autonomy-tier",
      element,
      label: "Autonomy tier",
      getValue: () => getGovernance(element)?.autonomyTier || "",
      setValue: (value) =>
        updateGovernance(element, { autonomyTier: value || undefined }, svc),
      getOptions: () => [
        {
          value: "",
          label: `Default${fallback ? ` (Tier ${fallback})` : ""}`,
        },
        { value: "1", label: "Tier 1 — approval required" },
        { value: "2", label: "Tier 2 — confirm & log" },
        { value: "3", label: "Tier 3 — fully autonomous" },
      ],
    });
  }

  function group(element) {
    const type = element.businessObject.$type;
    const entries = [];
    if (type === "bpmn:ServiceTask") {
      entries.push({
        id: "twoa-action",
        component: ActionKeyEntry,
        isEdited: isSelectEntryEdited,
      });
    }
    if (type === "bpmn:UserTask") {
      entries.push({
        id: "twoa-profile",
        component: ProfileEntry,
        isEdited: isSelectEntryEdited,
      });
    }
    entries.push({
      id: "twoa-tier",
      component: TierEntry,
      isEdited: isSelectEntryEdited,
    });
    return {
      id: "twoa-governance",
      label: "Governance (2nd Act)",
      entries,
      component: Group,
    };
  }

  function GovernanceProvider(propertiesPanel) {
    this.getGroups = (element) => (groups) => {
      if (GOVERNED_TYPES.includes(element.businessObject?.$type)) {
        groups.push(group(element));
      }
      return groups;
    };
    propertiesPanel.registerProvider(500, this);
  }
  GovernanceProvider.$inject = ["propertiesPanel"];

  return {
    __init__: ["twoaGovernanceProvider"],
    twoaGovernanceProvider: ["type", GovernanceProvider],
  };
}

export default function WorkflowDiagramEditor({ workflow }) {
  const canvasRef = useRef(null);
  const panelRef = useRef(null);
  const modelerRef = useRef(null);

  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [versionNumber, setVersionNumber] = useState(
    workflow.current_version.version_number,
  );

  useEffect(() => {
    let modeler;
    let cancelled = false;

    async function init() {
      const [{ default: BpmnModeler }, ppPanel, panelPrimitives] =
        await Promise.all([
          import("bpmn-js/lib/Modeler"),
          import("bpmn-js-properties-panel"),
          import("@bpmn-io/properties-panel"),
        ]);

      if (cancelled) return;

      const defaults = {};
      for (const s of workflow.steps || []) {
        defaults[s.step_key] = s.autonomy_tier;
      }

      const governanceModule = createGovernanceModule({
        panel: panelPrimitives,
        pp: ppPanel,
        profiles: workflow.profiles || [],
        actions: workflow.actions || [],
        defaults,
      });

      modeler = new BpmnModeler({
        container: canvasRef.current,
        propertiesPanel: { parent: panelRef.current },
        additionalModules: [
          ppPanel.BpmnPropertiesPanelModule,
          ppPanel.BpmnPropertiesProviderModule,
          governanceModule,
        ],
        moddleExtensions: { twoa: twoaModdle },
      });
      modelerRef.current = modeler;

      try {
        await modeler.importXML(workflow.current_version.bpmn_xml);
        modeler.get("canvas").zoom("fit-viewport");
        if (!cancelled) setReady(true);
      } catch (e) {
        if (!cancelled) setError(`Could not render diagram: ${e.message}`);
      }
    }

    init();

    return () => {
      cancelled = true;
      if (modeler) modeler.destroy();
      modelerRef.current = null;
    };
    // Initialise once for this workflow version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSave() {
    if (!modelerRef.current) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const { xml } = await modelerRef.current.saveXML({ format: true });
      const res = await saveWorkflowVersionAction(workflow.id, xml);
      if (res.ok) {
        setVersionNumber(res.version.version_number);
        setMessage(`Saved as version ${res.version.version_number}.`);
      } else {
        setError(res.error || "Save failed.");
      }
    } catch (e) {
      setError(e.message || "Could not serialize the diagram.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-6 space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex items-center rounded-full bg-gold-light px-2 py-0.5 text-[11px] font-medium text-navy">
          Current v{versionNumber}
        </span>
        <p className="text-sm text-text-muted">
          Edit the diagram and per-step governance, then save a new version.
        </p>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !ready}
          className="ml-auto rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
        >
          {saving ? "Saving…" : "Save new version"}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-border bg-bg-card px-4 py-2 text-sm text-[#9B2335]">
          {error}
        </div>
      )}
      {message && (
        <div className="rounded-md border border-border bg-bg-card px-4 py-2 text-sm text-text-primary">
          {message}
        </div>
      )}

      <div
        className="flex overflow-hidden rounded-lg border bg-bg-card"
        style={{ borderColor: "#ece8dd", height: "640px" }}
      >
        <div ref={canvasRef} className="min-w-0 flex-1" />
        <div
          ref={panelRef}
          className="w-80 shrink-0 overflow-y-auto border-l"
          style={{ borderColor: "#ece8dd" }}
        />
      </div>
    </div>
  );
}
