import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from services.org_settings import DEFAULT_SETTINGS, category_for  # noqa: E402
from services.rbac import can_manage_org_settings  # noqa: E402

print("routes:", [r.path for r in main.app.routes
                  if "/orgs" in r.path or "theme" in r.path])
print("defaults:", len(DEFAULT_SETTINGS))
print("category(brand.color.navy) ->", category_for("brand.color.navy"))

RIPASSO = "bb347258-8f28-4f49-8cc9-e29ccad82884"
HOME = "00000000-0000-0000-0000-000000000001"
OTHER = "5e2015b4-f04a-488d-b268-c9f065dec1a5"

sa = {"role": "super_admin", "org_id": RIPASSO}
oa = {"role": "org_admin", "org_id": HOME}
mb = {"role": "member", "org_id": HOME}

print("super_admin -> other org:", can_manage_org_settings(sa, OTHER))
print("org_admin   -> own org  :", can_manage_org_settings(oa, HOME))
print("org_admin   -> other org:", can_manage_org_settings(oa, OTHER))
print("member      -> own org  :", can_manage_org_settings(mb, HOME))
