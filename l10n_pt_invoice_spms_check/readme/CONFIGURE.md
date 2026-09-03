- Assign the *SPMS / Consultation* group to users who can read the
  check results, and *SPMS / Responsible* to the users allowed to work
  with them and with the generated credit notes.
- The polling scheduled action (*SPMS: get conference results from the
  CCF*, under *Settings → Technical → Automation → Scheduled Actions*)
  runs hourly out of the box. It only acts on invoices already sent to
  SPMS that still have no definitive check result, with each company's
  own SPMS credentials, so a database where nothing was sent makes no
  calls. To pause the polling, deactivate the scheduled action — never
  uninstall the module just to stop the calls.
- Set the *SPMS Adjustment Line Product* on the company (SPMS page, visible
  to *Technical Settings* users): a sale service product required to append
  the adjustment line when an official value differs from the itemised
  total. Credit notes that need it are skipped with a clear message until
  it is configured.
