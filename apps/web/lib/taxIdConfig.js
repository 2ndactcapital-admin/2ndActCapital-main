// Tax ID type configuration: display label, input format hint, masking rule,
// and default country. Used by the Tax IDs tab to drive the add form and the
// masked display.
export const TAX_ID_TYPES = [
  { value: "ssn", label: "SSN", format: "###-##-####", mask: "***-**-####", country: "US" },
  { value: "ein", label: "EIN", format: "##-#######", mask: "**-#######", country: "US" },
  { value: "itin", label: "ITIN", format: "9##-##-####", mask: "***-**-####", country: "US" },
  { value: "utr", label: "UTR", format: "##########", mask: "******####", country: "GB" },
  { value: "vat", label: "VAT", format: "CC#########", mask: "CC*****####", country: "" },
  { value: "trn", label: "TRN", format: "#########", mask: "*****####", country: "KY" },
  { value: "nino", label: "NI Number", format: "AA######A", mask: "**####A", country: "GB" },
  { value: "tin_other", label: "Tax ID", format: "", mask: "last 4 visible", country: "" },
];

export const TAX_ID_CONFIG = Object.fromEntries(
  TAX_ID_TYPES.map((t) => [t.value, t]),
);

export function taxIdConfig(type) {
  return TAX_ID_CONFIG[type] || TAX_ID_CONFIG.tin_other;
}

export function taxIdLabel(type) {
  return taxIdConfig(type).label;
}
