// BUGFIX3 (1d) — a Decimal string in scientific notation ("9.0E+5", "1E+6", "1.25e-3") → exact fixed-point text
// ("900000", "1000000", "0.00125"), by moving the decimal point in the STRING — never Number()/parseFloat (a JS
// number cannot hold every base-10 amount). The API now serialises fixed-point everywhere (services.money_text),
// and this is the render-side guard: every money formatter runs its input through it, so a stray exponent can
// never again render as "$9.0E".

const EXP = /^([+-]?)(\d*)(?:\.(\d*))?[eE]([+-]?\d+)$/;

export function toFixedPoint(value) {
  if (value === null || value === undefined) return value;
  const s = String(value).trim();
  const m = EXP.exec(s);
  if (!m) return s;
  const [, sign, intPart = "", fracPart = "", expText] = m;
  const exp = Number.parseInt(expText, 10);
  let digits = `${intPart}${fracPart}`;
  let point = intPart.length + exp; // position of the decimal point within `digits`
  if (point <= 0) {
    digits = "0".repeat(1 - point) + digits;
    point = 1;
  } else if (point > digits.length) {
    digits += "0".repeat(point - digits.length);
  }
  let whole = digits.slice(0, point).replace(/^0+(?=\d)/, "");
  const frac = digits.slice(point);
  if (whole === "") whole = "0";
  const neg = sign === "-" && /[1-9]/.test(digits);
  return `${neg ? "-" : ""}${whole}${frac ? `.${frac}` : ""}`;
}
