This module stores the SPMS/CCMSNS invoice check results ("Conferência de
Faturas") of the customer invoices sent to SPMS and manages the resulting
rectifying credit notes:

- Stores one check result per invoice — the CCF document number and
  date, the check state, the read and recomputed totals, the official
  credit value (TotalFaturaIVALido − TotalFaturaIVACalculado, exactly as
  the check document states it) and the ofício text.
- Stores the check breakdown the way the document states it: one line
  per claim (prescription) carrying the read and recomputed claim totals
  and their difference, and under it every error the check reports with
  its level (claim, line, prescription data); the errors anchored to the
  invoice itself or to a lot hang from the result. A sum over the lines
  is a sum over the prescriptions, and "all C012 errors" is one filter
  away.
- Generates the draft rectifying invoice (credit note) for the results
  that came back with errors, through the standard reversal mechanism, carrying
  exactly the official value: one line per rejected prescription plus a
  deterministic adjustment line when needed.
- Keeps the sale order closed: the CCF cut is definitive, so the credited
  quantities are not returned as pending to invoice on the sale order the
  invoice came from.
