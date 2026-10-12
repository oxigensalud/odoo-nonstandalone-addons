- The polling queue has no cap or back-off: an invoice the CCF never
  answers (a permanent 301) is asked again every pass, forever. Harmless
  at the current volumes — one read-only call per invoice and pass — but
  revisit if the stuck tail ever grows enough to matter.
- A prescription the parser cannot match to an original invoice line
  blocks the generation of its invoice, with no manual exclusion lever:
  revisit if a real case ever needs one.
- When the claims of one invoice mix positive and negative differences,
  two rules collide: the Portuguese certification refuses a note with
  lines of both signs, and SPMS accepts one note per invoice. Those claims
  fit neither in two notes nor in one note with a line each, so the
  module generates the note as one line with the net amount, on one unit
  of the company's *SPMS Regularization Product* (see *Configuration*).
  This has side effects, only on such notes: the note shows no detail per
  prescription — the line lists the prescriptions in its prescription
  field, and the detail stays in the verification result —; the line has
  no dates, and keeps the user number, beneficiary number, context,
  prescription type and suspension reason only when all the prescriptions
  share them; and it has no link to the sale order lines, so the order
  neither lists the note nor counts its amount as credited. Notes whose
  claims share one sign keep one line per prescription.
