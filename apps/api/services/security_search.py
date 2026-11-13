"""PORTENTRY2 (MUSE-OLIVO-P.1) — the security master as a READ-ONLY search for internal staff.

The master (portfolio.securities_global + securities_global_identifiers, 15,959 securities loaded by secmaster1) is
global data with ``SELECT USING (true)``; its admin screen stays Super Admin (routers.security_master). Held-away entry
and any security picker need to FIND a security by ticker, name, CUSIP, ISIN or FIGI. One query, ranked:

  0  an identifier equal to the query (the ticker normalised the way every ticker write is — security_master_symbols)
  1  a name starting with the query
  2  a name containing every word of the query / an identifier starting with it

Merged-away rows and closed versions are never returned. No write path exists here.
"""
from __future__ import annotations

from services.security_master_symbols import normalize_symbol

LIMIT_MAX = 50


async def search(conn, q: str, *, limit: int = 20) -> list[dict]:
    q = (q or "").strip()
    if len(q) < 1:
        return []
    limit = max(1, min(int(limit), LIMIT_MAX))
    ticker = normalize_symbol(q) or q.upper()
    words = [w for w in q.replace(",", " ").split() if w][:6]
    rows = await conn.fetch(
        """
        WITH ids AS (
          SELECT i.global_security_id AS sid,
                 min(CASE WHEN upper(i.id_value) = upper($1) OR (i.id_type = 'ticker' AND i.id_value = $2) THEN 0
                          WHEN upper(i.id_value) LIKE upper($1) || '%' THEN 2 END) AS r
          FROM portfolio.securities_global_identifiers i
          WHERE i.valid_to IS NULL AND i.system_to IS NULL
            AND (upper(i.id_value) = upper($1) OR i.id_value = $2 OR upper(i.id_value) LIKE upper($1) || '%')
          GROUP BY 1
        ), names AS (
          SELECT s.id AS sid, CASE WHEN s.name ILIKE $1 || '%' THEN 1 ELSE 2 END AS r
          FROM portfolio.securities_global s
          WHERE s.valid_to IS NULL AND s.system_to IS NULL
            AND (SELECT bool_and(s.name ILIKE '%' || w || '%' OR coalesce(s.short_name, '') ILIKE '%' || w || '%')
                 FROM unnest($3::text[]) w)
        ), hits AS (
          SELECT sid, min(r) AS r FROM (SELECT * FROM ids UNION ALL SELECT * FROM names) u GROUP BY sid
        )
        SELECT s.id::text AS id, s.name, s.security_type, s.currency_code, h.r AS rank,
               (SELECT jsonb_object_agg(x.id_type, x.id_value) FROM (
                  SELECT DISTINCT ON (i.id_type) i.id_type, i.id_value
                  FROM portfolio.securities_global_identifiers i
                  WHERE i.global_security_id = s.id AND i.valid_to IS NULL AND i.system_to IS NULL
                  ORDER BY i.id_type, i.is_primary DESC, i.is_alternate, i.id_value) x)::text AS identifiers
        FROM hits h JOIN portfolio.securities_global s ON s.id = h.sid
        WHERE s.valid_to IS NULL AND s.system_to IS NULL AND s.merged_into_id IS NULL
        ORDER BY h.r, s.name
        LIMIT $4
        """,
        q, ticker, words or [q], limit)
    import json

    return [{"id": r["id"], "name": r["name"], "security_type": r["security_type"],
             "currency_code": r["currency_code"], "rank": int(r["rank"]),
             "identifiers": json.loads(r["identifiers"]) if r["identifiers"] else {}} for r in rows]
