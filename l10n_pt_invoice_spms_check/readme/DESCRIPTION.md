This module stores the SPMS/CCMSNS invoice check results ("Conferência de
Faturas") of the customer invoices sent to SPMS and manages the resulting
rectifying credit notes:

- Stores one check result per invoice — check state, the read and
  recomputed totals, the official credit value (TotalFaturaIVALido −
  TotalFaturaIVACalculado, exactly as the check document states it) and
  the ofício text.
- Stores every error the check reports, whatever its nesting point, in
  a single table: one row per error with its level (invoice, lot, claim,
  line, prescription data) and the anchor of that level, so "all C012 rows"
  is one filter away.
- Generates the draft rectifying invoice (credit note) for the results
  that came back with errors, through the standard reversal mechanism, carrying
  exactly the official value: one line per rejected prescription plus a
  deterministic adjustment line when needed.
- Keeps the sale order closed: the CCF cut is definitive, so the credited
  quantities are not returned as pending to invoice on the sale order the
  invoice came from.
