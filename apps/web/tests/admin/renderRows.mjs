// USERMGMT1 verify helper — render the REAL UserRow / SignInActions /
// PendingPanel for rows taken from a live GET /admin/users response and print,
// per row id, the data-action controls each rendered. Usage:
//   node tests/admin/renderRows.mjs <rows.json>
// Prints one JSON object: { "<id>": ["edit", "reset-password", …], … }.
import { readFileSync } from "node:fs";
import { register } from "node:module";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const parts = await import("../../components/admin/PeopleParts.jsx");

const rows = JSON.parse(readFileSync(process.argv[2], "utf8"));
const noop = () => {};
const out = {};
for (const user of rows) {
  const html = [
    renderToStaticMarkup(h("table", null, h("tbody", null, h(parts.UserRow, { user, onEdit: noop, onRevoke: noop })))),
    renderToStaticMarkup(h(parts.SignInActions, { user })),
    user.status === "pending"
      ? renderToStaticMarkup(h(parts.PendingPanel, {
          pending: [user], users: rows, roles: [{ id: "r", name: "member" }],
          onApprove: noop, onReject: noop, onLink: noop,
        }))
      : "",
  ].join("");
  out[user.id] = [...html.matchAll(/data-action="([^"]+)"/g)].map((m) => m[1]).sort();
}
process.stdout.write(JSON.stringify(out));
