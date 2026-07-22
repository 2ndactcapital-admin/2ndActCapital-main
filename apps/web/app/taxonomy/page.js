import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import TaxonomyBrowser from "@/components/TaxonomyBrowser";
import { getTaxonomy } from "@/lib/api";
import { brandName, loadTheme } from "@/lib/theme";

export async function generateMetadata() {
  const theme = await loadTheme();
  const brand = brandName(theme.settings || {});
  return { title: brand ? `Asset Taxonomy — ${brand}` : "Asset Taxonomy" };
}

export default async function TaxonomyPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/taxonomy");
  }

  let taxonomy = { super_classes: [] };
  try {
    taxonomy = await getTaxonomy();
  } catch {
    // render empty state
  }

  const totalMajorClasses = (taxonomy.super_classes || []).reduce(
    (sum, sc) => sum + (sc.major_classes?.length || 0),
    0
  );
  const totalSubCategories = (taxonomy.super_classes || []).reduce(
    (sum, sc) =>
      sum +
      (sc.major_classes || []).reduce(
        (s, mc) => s + (mc.sub_categories?.length || 0),
        0
      ),
    0
  );

  return (
    <AppShell user={session.user}>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Asset Taxonomy</h1>
          <p className="mt-1 text-sm text-text-muted">
            Investment asset classification reference
          </p>
        </div>
        <div className="flex gap-6 text-right">
          <div>
            <p className="text-2xl font-semibold text-navy">
              {taxonomy.super_classes?.length || 0}
            </p>
            <p className="text-xs text-text-muted">Super-classes</p>
          </div>
          <div>
            <p className="text-2xl font-semibold text-navy">{totalMajorClasses}</p>
            <p className="text-xs text-text-muted">Asset classes</p>
          </div>
          <div>
            <p className="text-2xl font-semibold text-navy">{totalSubCategories}</p>
            <p className="text-xs text-text-muted">Sub-categories</p>
          </div>
        </div>
      </div>

      <div className="mt-8">
        <TaxonomyBrowser taxonomy={taxonomy} />
      </div>
    </AppShell>
  );
}
