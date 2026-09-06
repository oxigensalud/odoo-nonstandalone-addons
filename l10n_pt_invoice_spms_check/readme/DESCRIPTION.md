This module stores the SPMS/CCMSNS invoice check results ("Conferência de
Faturas") of the customer invoices sent to SPMS and manages the resulting
rectifying credit and debit notes:

- Stores one check result per invoice — the CCF document number and
  date, the check state, the read and recomputed totals, the official
  credit value (TotalFaturaIVALido − TotalFaturaIVACalculado, exactly as
  the check document states it) and the official result notice, the
  CCF's ofício.
- Stores the check breakdown the way the document states it: one line
  per claim (prescription) carrying the read and recomputed claim totals
  and their difference, and under it every error the check reports with
  its level (claim, line, prescription data); the errors anchored to the
  invoice itself hang from the result. A sum over the lines is a sum over
  the prescriptions, and "all C012 errors" is one filter away.
- Generates the draft rectifying document for the results that came back
  with errors — a credit note through the standard reversal mechanism, or
  a debit note through the standard debit-note flow when the official
  value is negative (the check computed more than billed) — carrying
  exactly the official value: one line per affected prescription, with
  the rounding cent of the tax written on the tax line when needed.
- Keeps the sale order closed: the CCF cut is definitive, so the credited
  quantities are not returned as pending to invoice on the sale order the
  invoice came from, and the debit note's lines carry no link to it.
