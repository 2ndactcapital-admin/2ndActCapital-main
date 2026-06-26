"use client";

import { useState } from "react";
import EntityDetailsForm from "@/components/crm/EntityDetailsForm";
import AttributesSection from "@/components/crm/AttributesSection";
import OwnershipTree from "@/components/crm/OwnershipTree";
import AddressesTab from "@/components/crm/tabs/AddressesTab";
import EmploymentTab from "@/components/crm/tabs/EmploymentTab";
import TaxIdsTab from "@/components/crm/tabs/TaxIdsTab";
import SocialTab from "@/components/crm/tabs/SocialTab";
import ComplianceTab from "@/components/crm/tabs/ComplianceTab";
import NotesTab from "@/components/crm/tabs/NotesTab";

export default function EntityDetailTabs({ full, graph }) {
  const entity = full.entity;
  const isIndividual = entity.entity_type === "individual";
  const hasHoldings = (full.holdings || []).length > 0;

  const tabs = [
    { key: "overview", label: "Overview" },
    { key: "addresses", label: "Addresses" },
    ...(isIndividual ? [{ key: "employment", label: "Employment" }] : []),
    { key: "tax_ids", label: "Tax IDs" },
    { key: "social", label: "Social Profiles" },
    { key: "notes", label: "Notes" },
    { key: "compliance", label: "Compliance" },
  ];

  const [active, setActive] = useState("overview");

  return (
    <div>
      {/* Tab nav */}
      <div className="flex flex-wrap gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setActive(t.key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              active === t.key
                ? "border-navy text-navy"
                : "border-transparent text-text-muted hover:text-text-secondary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="mt-6">
        {/* Overview */}
        <div className={active === "overview" ? "" : "hidden"}>
          <div className="grid gap-8 lg:grid-cols-2">
            <div className="rounded-lg border border-border bg-bg-card p-6">
              <EntityDetailsForm entity={entity} />
            </div>
            <div className="rounded-lg border border-border bg-bg-card p-6">
              <h2 className="text-sm font-semibold text-text-secondary">Ownership</h2>
              <div className="mt-4 space-y-6">
                <OwnershipTree graph={graph} rootId={entity.id} direction="up" title="Owned by" />
                {hasHoldings && (
                  <OwnershipTree graph={graph} rootId={entity.id} direction="down" title="Owns" />
                )}
              </div>
            </div>
          </div>
          <div className="mt-8 max-w-3xl">
            <AttributesSection entityId={entity.id} attributes={full.attributes || []} />
          </div>
          <div className="mt-6">
            <a
              href={`/investment-profile?entity=${entity.id}&tab=brief`}
              className="text-sm font-medium text-navy hover:underline"
            >
              View Client Brief →
            </a>
          </div>
        </div>

        {/* Addresses */}
        <div className={active === "addresses" ? "" : "hidden"}>
          <AddressesTab entityId={entity.id} initial={full.addresses || []} />
        </div>

        {/* Employment (individuals only) */}
        {isIndividual && (
          <div className={active === "employment" ? "" : "hidden"}>
            <EmploymentTab entityId={entity.id} initial={full.employment || []} />
          </div>
        )}

        {/* Tax IDs */}
        <div className={active === "tax_ids" ? "" : "hidden"}>
          <TaxIdsTab entityId={entity.id} initial={full.tax_ids || []} />
        </div>

        {/* Social */}
        <div className={active === "social" ? "" : "hidden"}>
          <SocialTab entityId={entity.id} initial={full.social_profiles || []} />
        </div>

        {/* Notes */}
        <div className={active === "notes" ? "" : "hidden"}>
          <NotesTab entityId={entity.id} initial={full.notes || []} />
        </div>

        {/* Compliance */}
        <div className={active === "compliance" ? "" : "hidden"}>
          <ComplianceTab entityId={entity.id} initial={full.compliance_record} />
        </div>
      </div>
    </div>
  );
}
