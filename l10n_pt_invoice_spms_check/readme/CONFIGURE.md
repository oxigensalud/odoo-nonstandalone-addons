- Assign the *SPMS / Consultation* group to users who can read the
  check results, and *SPMS / Responsible* to the users allowed to work
  with them and with the generated credit notes.
- The polling scheduled action (*SPMS: get conference results from the
  CCF*, under *Settings → Technical → Automation → Scheduled Actions*)
  runs hourly out of the box. It only acts on invoices already sent to
  SPMS that still have no definitive check result, with each company's
  own SPMS credentials, so a database where nothing was sent makes no
  calls. Each invoice is asked in its own queue job: a transport
  failure is retried by the job itself, any other failure stays visible
  under *Queue → Jobs* and the next pass queues a fresh attempt. To
  pause the polling, deactivate the scheduled action — never uninstall
  the module just to stop the calls.
- Set the *SPMS Adjustment Line Product* on the company (SPMS page, visible
  to *Technical Settings* users): a sale service product required to append
  the adjustment line when an official value differs from the itemised
  total. Credit notes that need it are skipped with a clear message until
  it is configured.
- Set the *SPMS Adjustment Limit* on the company (same page, 0.05 by
  default): the largest difference, taxes included, between the official
  value and the credit-note lines total that the adjustment line may
  absorb. A larger difference holds the result in *Error* for review
  instead of generating the credit note.
