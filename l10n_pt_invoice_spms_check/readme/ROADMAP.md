- The polling queue has no cap or back-off: an invoice that never gets
  a definitive answer (e.g. a permanent 301 anomaly) is retried every
  pass, forever. Harmless at the current volumes — one read-only call
  per invoice and pass — but revisit if the stuck tail ever grows
  enough to matter.
- A prescription the parser cannot match to an original invoice line
  blocks the generation of its invoice, with no manual exclusion lever:
  revisit if a real case ever needs one.
