- The polling queue has no cap or back-off: an invoice the CCF never
  answers (a permanent 301) is asked again every run, forever. Harmless
  at the current volumes — one read-only call per invoice and run — but
  revisit if the stuck tail ever grows enough to matter.
- A prescription the parser cannot match to an original invoice line
  blocks the generation of its invoice, with no manual exclusion lever:
  revisit if a real case ever needs one.
